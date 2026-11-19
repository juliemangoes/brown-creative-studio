"""Brand profile, stages and sign-off, brand files with signed links, and the portal Brand Hub."""

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail, signing
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.brand.models import BrandAsset, BrandStage
from apps.brand.services import add_asset, link_brand_project, profile_for, send_for_signoff
from apps.projects.services import create_from_template, ensure_builtin_templates
from apps.studio import files, scanning
from apps.studio.permissions import ensure_client_roles
from apps.studio.tests.helpers import make_studio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'


@pytest.fixture
def studio(db):
    people = make_studio()
    roles = ensure_client_roles(people["org"])
    # The skincare client user is a billing contact only (no approval rights).
    from apps.members.models import WorkspaceMembership

    WorkspaceMembership.objects.filter(user=people["skincare_client"]).update(
        custom_role=roles["Client billing contact"]
    )
    templates = ensure_builtin_templates(people["org"])
    people["brand_project"] = create_from_template(
        people["restaurant"],
        templates["brand"],
        name="Casa Maya rebrand",
        starts_on=timezone.localdate(),
        owner=people["owner"],
    )
    people["stages"] = link_brand_project(people["brand_project"])
    return people


def _ws(name, ws, **kw):
    return reverse(name, kwargs={"workspace_id": ws.id, **kw})


def portal_login(client, user, workspace):
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(workspace.id)
    session.save()


# ── Profile ──────────────────────────────────────────────────────────────────


def test_profile_save_parses_lists_pairs_and_colours(client, studio):
    client.force_login(studio["editor"])
    colors = [{"name": "Cacao", "hex": "6b4f3a", "role": "Primary"}, {"name": "", "hex": "", "role": ""}]
    response = client.post(
        _ws("brand:profile_save", studio["restaurant"]),
        {
            "purpose": "Feed the town well.",
            "values": "Warmth\n- Family\n\n",
            "audiences": "Locals: lunch regulars\nTourists: weekend",
            "fonts": "Instrument Serif: headings",
            "tagline": "Comida de casa",
            "internal_notes": "Owner hates purple",
            "colors_json": json.dumps(colors),
        },
    )
    assert response.status_code == 302
    profile = profile_for(studio["restaurant"])
    assert profile.values == ["Warmth", "Family"]
    assert profile.audiences == [
        {"name": "Locals", "description": "lunch regulars"},
        {"name": "Tourists", "description": "weekend"},
    ]
    assert profile.fonts == [{"name": "Instrument Serif", "usage": "headings"}]
    assert profile.colors == [{"name": "Cacao", "hex": "#6B4F3A", "role": "Primary"}]
    assert "Owner hates purple" not in profile.as_brief() and "Comida de casa" in profile.as_brief()


def test_bad_colour_is_refused(client, studio):
    client.force_login(studio["owner"])
    response = client.post(
        _ws("brand:profile_save", studio["restaurant"]), {"colors_json": json.dumps([{"hex": "#12"}])}
    )
    assert response.status_code == 400


def test_viewer_cannot_edit_and_clients_cannot_open(client, studio):
    client.force_login(studio["viewer"])
    assert client.get(_ws("brand:profile", studio["restaurant"])).status_code == 200
    assert client.post(_ws("brand:profile_save", studio["restaurant"]), {"purpose": "x"}).status_code == 403
    assert client.post(_ws("brand:publish", studio["restaurant"]), {"publish": "1"}).status_code == 403
    client.force_login(studio["restaurant_client"])
    for name in ("brand:profile", "brand:stages", "brand:files"):
        assert client.get(_ws(name, studio["restaurant"])).status_code == 403


@pytest.mark.parametrize("name", ["brand:profile", "brand:stages", "brand:files"])
def test_brand_pages_of_another_client_are_forbidden(client, studio, name):
    client.force_login(studio["editor"])  # restaurant only
    assert client.get(_ws(name, studio["skincare"])).status_code == 403


# ── Stages and sign-off ──────────────────────────────────────────────────────


def test_brand_project_creates_seven_stages_linked_to_milestones(studio):
    stages = studio["stages"]
    assert [s.key for s in stages] == [
        "discovery",
        "research",
        "strategy",
        "verbal",
        "visual",
        "identity",
        "guidelines",
    ]
    assert all(s.milestone and s.milestone.template_key == f"{s.key}_signoff" for s in stages)
    assert stages[0].status == "in_progress" and stages[5].revision_limit == 2


def test_projects_page_links_brand_stages_when_a_brand_project_is_created(client, studio):
    template = ensure_builtin_templates(studio["org"])["strategy"]
    client.force_login(studio["owner"])
    client.post(
        _ws("projects:create", studio["skincare"]),
        {"template": template.id, "name": "Luma strategy", "starts_on": "2026-12-01"},
    )
    assert list(BrandStage.objects.filter(workspace=studio["skincare"]).values_list("key", flat=True)) == [
        "discovery",
        "research",
        "strategy",
        "verbal",
    ]


def test_send_for_signoff_emails_approvers_and_is_audited(studio):
    ensure_client_roles(studio["org"])
    stage = studio["stages"][0]
    sent = send_for_signoff(stage, studio["owner"], "Goals and interviews summarised.")
    assert sent == 1 and mail.outbox[0].to == ["ana@casamaya.test"] and "/portal/" in mail.outbox[0].body
    assert AuditEvent.objects.filter(verb="brand.sent_for_signoff", workspace=studio["restaurant"]).exists()


def test_client_signs_off_in_the_portal(client, studio):
    stage = studio["stages"][0]
    send_for_signoff(stage, studio["owner"], "Discovery summary")
    portal_login(client, studio["restaurant_client"], studio["restaurant"])
    hub = client.get(reverse("brand_portal:hub"))
    assert hub.status_code == 200 and "Discovery summary" in hub.content.decode()
    response = client.post(reverse("brand_portal:sign_off", kwargs={"stage_id": stage.id}))
    assert response.status_code == 302
    stage.refresh_from_db()
    assert stage.status == "signed_off" and stage.signed_off_by == studio["restaurant_client"]
    stage.milestone.refresh_from_db()
    assert stage.milestone.status == "done"
    assert BrandStage.objects.get(workspace=studio["restaurant"], key="research").status == "in_progress"
    event = AuditEvent.objects.get(verb="brand.stage_signed_off")
    assert event.actor == studio["restaurant_client"]
    # A second sign-off of the same stage is refused.
    assert client.post(reverse("brand_portal:sign_off", kwargs={"stage_id": stage.id})).status_code == 404


def test_identity_changes_count_revisions(client, studio):
    identity = next(s for s in studio["stages"] if s.key == "identity")
    send_for_signoff(identity, studio["owner"], "Logo round 1")
    portal_login(client, studio["restaurant_client"], studio["restaurant"])
    assert (
        client.post(reverse("brand_portal:changes", kwargs={"stage_id": identity.id}), {"comment": ""}).status_code
        == 302
    )
    identity.refresh_from_db()
    assert identity.status == "awaiting_signoff"  # an empty request changes nothing
    client.post(reverse("brand_portal:changes", kwargs={"stage_id": identity.id}), {"comment": "Warmer brown please"})
    identity.refresh_from_db()
    assert (
        identity.status == "changes_requested"
        and identity.revisions_used == 1
        and identity.client_comment == "Warmer brown please"
    )


def test_portal_signoff_is_scoped_and_needs_approval_rights(client, studio):
    stage = studio["stages"][0]
    send_for_signoff(stage, studio["owner"], "Discovery")
    # Another client's user (skincare billing contact) cannot see or sign the restaurant's stage.
    portal_login(client, studio["skincare_client"], studio["skincare"])
    assert (
        client.post(reverse("brand_portal:sign_off", kwargs={"stage_id": stage.id})).status_code == 403
    )  # no portal_approve
    # Even with approval rights, a stage id from another client is a 404.
    from apps.members.models import WorkspaceMembership

    WorkspaceMembership.objects.filter(user=studio["skincare_client"]).update(custom_role=None)
    assert client.post(reverse("brand_portal:sign_off", kwargs={"stage_id": stage.id})).status_code == 404
    stage.refresh_from_db()
    assert stage.status == "awaiting_signoff"


# ── Files ────────────────────────────────────────────────────────────────────


def test_upload_stores_privately_and_scans(client, studio):
    client.force_login(studio["editor"])
    client.post(
        _ws("brand:files_upload", studio["restaurant"]),
        {
            "files": [
                SimpleUploadedFile("logo.svg", SVG),
                SimpleUploadedFile("logo.png", PNG),
                SimpleUploadedFile("bad.pdf", b"%PDF-1.4 " + scanning.EICAR),
            ],
            "kind": "logo",
            "title": "Casa Maya",
            "variant": "Primary",
            "client_visible": "on",
        },
    )
    assets = BrandAsset.objects.filter(workspace=studio["restaurant"])
    assert sorted(a.file_format for a in assets) == ["png", "svg"]
    asset = assets.get(file_format="png")
    path = Path(asset.file.path)
    assert path.exists() and str(path).startswith(str(Path(settings.PRIVATE_MEDIA_ROOT).resolve()))
    assert not str(path).startswith(str(Path(settings.MEDIA_ROOT).resolve() / "clients"))
    assert asset.scan_status == "not_scanned" and asset.original_name == "logo.png"


def test_signed_download_rechecks_access(client, studio):
    asset = add_asset(
        studio["restaurant"], SimpleUploadedFile("logo.png", PNG), user=studio["owner"], kind="logo", title="Logo"
    )
    client.force_login(studio["owner"])
    response = client.get(_ws("brand:file", studio["restaurant"], asset_id=asset.id))
    signed = response["Location"]
    assert response.status_code == 302 and signed.startswith("/files/")
    served = client.get(signed)
    assert served.status_code == 200 and b"".join(served.streaming_content) == PNG
    assert served["X-Content-Type-Options"] == "nosniff" and "attachment" in served["Content-Disposition"]
    # The same link in the hands of staff not on this client, or another client's user: refused.
    client.force_login(studio["editor"])
    assert client.get(signed).status_code == 200  # editor works on the restaurant
    from apps.studio.tests.helpers import make_user

    outsider = make_user("outsider@studio.test", studio["org"])
    client.force_login(outsider)
    assert client.get(signed).status_code == 403
    portal_login(client, studio["skincare_client"], studio["skincare"])
    assert client.get(signed).status_code == 403
    # The restaurant's own client can't download a studio file that isn't shared.
    portal_login(client, studio["restaurant_client"], studio["restaurant"])
    assert client.get(signed).status_code == 403
    assert client.get(reverse("brand_portal:download", kwargs={"asset_id": asset.id})).status_code == 404
    asset.client_visible = True
    asset.save()
    assert (
        client.get(client.get(reverse("brand_portal:download", kwargs={"asset_id": asset.id}))["Location"]).status_code
        == 200
    )


def test_tampered_and_expired_links(client, studio, settings):
    asset = add_asset(
        studio["restaurant"], SimpleUploadedFile("logo.png", PNG), user=studio["owner"], kind="logo", title="Logo"
    )
    client.force_login(studio["owner"])
    url = files.signed_file_url(asset)
    assert client.get(url[:-3] + "xyz/").status_code == 404
    token = signing.dumps({"m": "auth.user", "pk": str(studio["owner"].pk), "i": 0}, salt=files.SALT, compress=True)
    assert client.get(reverse("studio:file", kwargs={"token": token})).status_code == 404  # not a private file model
    settings.FILE_LINK_TTL = -1
    assert client.get(url).status_code == 410


def test_cross_client_asset_ids_are_404(client, studio):
    asset = add_asset(
        studio["skincare"], SimpleUploadedFile("logo.png", PNG), user=studio["owner"], kind="logo", title="Luma"
    )
    client.force_login(studio["owner"])
    assert client.get(_ws("brand:file", studio["restaurant"], asset_id=asset.id)).status_code == 404
    assert client.post(_ws("brand:file_delete", studio["restaurant"], asset_id=asset.id)).status_code == 404
    assert BrandAsset.objects.filter(pk=asset.pk).exists()


def test_malware_upload_is_refused_by_service(studio):
    with pytest.raises(ValidationError):
        add_asset(
            studio["restaurant"],
            SimpleUploadedFile("x.pdf", b"%PDF-1.4" + scanning.EICAR),
            user=studio["owner"],
            kind="other",
        )


# ── Brand Hub ────────────────────────────────────────────────────────────────


def test_brand_hub_shows_only_what_is_shared(client, studio):
    profile = profile_for(studio["restaurant"])
    profile.purpose, profile.internal_notes = "Feed the town well.", "Margins are thin"
    profile.colors = [{"name": "Cacao", "hex": "#6B4F3A", "role": "Primary"}]
    profile.save()
    portal_login(client, studio["restaurant_client"], studio["restaurant"])
    dashboard = client.get(reverse("client_portal:dashboard")).content.decode()
    assert "Brand Hub" not in dashboard  # nothing shared yet
    profile.is_published = True
    profile.save()
    dashboard = client.get(reverse("client_portal:dashboard")).content.decode()
    assert "Brand Hub" in dashboard
    hub = client.get(reverse("brand_portal:hub")).content.decode()
    assert "Feed the town well." in hub and "#6B4F3A" in hub and "107, 79, 58" in hub
    assert "Margins are thin" not in hub


def test_brand_hub_needs_content_permission(client, studio):
    profile = profile_for(studio["skincare"])
    profile.is_published = True
    profile.save()
    portal_login(client, studio["skincare_client"], studio["skincare"])  # billing contact
    assert client.get(reverse("brand_portal:hub")).status_code == 403
