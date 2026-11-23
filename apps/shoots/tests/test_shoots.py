"""Shoots: conflicts, shot lists from planned posts, on-set sync, matching uploads, follow-ups, call sheets, scope."""

import json
import uuid
from datetime import time, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.clients.models import Contact
from apps.composer.models import PostMedia
from apps.planning.models import ContentPlan
from apps.planning.services import quick_plan
from apps.shoots import services
from apps.shoots.models import Shoot, Shot
from apps.studio.tests.helpers import join, make_account, make_studio, make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    people["account"] = make_account(people["restaurant"], "Casa Maya")
    people["freelancer"] = make_user("cam@freelance.test", people["org"], name="Cam")
    join(people["freelancer"], people["restaurant"], "contributor")
    return people


def _day(offset=5):
    return timezone.localdate() + timedelta(days=offset)


def _shoot(ws, title="Menu shoot", offset=5, call=time(9), wrap=time(13), **kw):
    return Shoot.objects.create(workspace=ws, title=title, date=_day(offset), call_time=call, wrap_time=wrap, **kw)


def _plan(studio, title, offset=10, **kw):
    return quick_plan(studio["restaurant"], studio["editor"], title=title, target_date=_day(offset), **kw)


# ── Conflicts and readiness ──────────────────────────────────────────────────


def test_conflicts_cover_people_gear_and_places_across_clients(studio):
    a = _shoot(studio["restaurant"], locations=["Casa Maya kitchen"], equipment=["Sony FX3", "Aputure 300d"])
    a.crew.add(studio["owner"])
    b = _shoot(studio["skincare"], "Luma flat-lays", call=time(11), wrap=time(15), locations=["casa maya kitchen"])
    b.equipment = ["sony fx3"]
    b.save()
    b.crew.add(studio["owner"])
    found = services.conflicts(a)
    assert any("Julian is also on Luma flat-lays" in f for f in found)
    assert any("Sony FX3 is also booked" in f for f in found)
    assert any("Casa Maya kitchen is also used" in f for f in found)
    b.call_time, b.wrap_time = time(14), time(16)  # no overlap any more
    b.save()
    assert services.conflicts(a) == []


def test_readiness_lists_what_is_missing(studio):
    shoot = _shoot(studio["restaurant"], talent=[{"name": "Rosa", "role": "chef", "release": False}])
    missing = services.readiness(shoot)
    assert "No shot list yet" in missing and "No crew" in missing and "No location" in missing
    assert "1 release form outstanding" in missing and "Client prep not sent" in missing
    shoot.crew.add(studio["editor"])
    shoot.locations, shoot.talent, shoot.prep_sent_at = ["Kitchen"], [{"name": "Rosa", "release": True}], timezone.now()
    shoot.save()
    services.add_shot(shoot, description="Plating", post=_plan(studio, "Plating").post)
    assert services.readiness(shoot) == []


def test_talent_release_is_recorded_from_the_form(studio):
    from apps.shoots.views import _talent

    assert _talent("Rosa Chi: chef (signed)") == {"name": "Rosa Chi", "role": "chef", "release": True}
    assert _talent("Don Pedro: owner") == {"name": "Don Pedro", "role": "owner", "release": False}


# ── Shot lists ───────────────────────────────────────────────────────────────


def test_shot_list_from_planned_posts_ties_each_shot_to_its_post(studio):
    shoot = _shoot(studio["restaurant"])
    taco = _plan(studio, "Taco Tuesday")
    taco.hook = "Close-up of the salsa pour"
    taco.save(update_fields=["hook"])
    _plan(studio, "Already shot").post.media_attachments.create(media_asset=_asset(studio["restaurant"]), position=0)
    ready = _plan(studio, "Ready", stage=ContentPlan.Stage.READY_FOR_REVIEW)
    candidates = list(services.candidate_posts(shoot))
    assert taco.post in candidates and ready.post not in candidates

    made = services.shots_from_posts(shoot, [taco.post], studio["editor"])
    assert [(s.code, s.description, s.post_id) for s in made] == [("S01", "Close-up of the salsa pour", taco.post.id)]
    taco.refresh_from_db()
    assert taco.stage == ContentPlan.Stage.SHOT_NEEDED
    assert taco.post not in list(services.candidate_posts(shoot))  # no duplicates
    assert services.next_code(shoot) == "S02"
    assert AuditEvent.objects.filter(verb="shoot.shot_added", target_id=str(taco.post.pk)).exists()


# ── On-set mode ──────────────────────────────────────────────────────────────


def test_on_set_sync_is_idempotent(client, studio):
    shoot = _shoot(studio["restaurant"])
    shoot.crew.add(studio["freelancer"])
    plan = _plan(studio, "Horchata")
    shot = services.add_shot(shoot, description="Horchata pour", post=plan.post)
    idea_id = str(uuid.uuid4())
    ops = [
        {"op": "status", "shot": str(shot.id), "status": "captured", "at": timezone.now().isoformat()},
        {"op": "note", "shot": str(shot.id), "text": "Use take 3"},
        {"op": "idea", "id": idea_id, "text": "Chef laughing at the pass", "status": "captured"},
        {"op": "bogus"},
    ]
    client.force_login(studio["freelancer"])
    url = reverse("shoots:sync", kwargs={"workspace_id": studio["restaurant"].id, "shoot_id": shoot.id})
    first = client.post(url, json.dumps({"ops": ops}), content_type="application/json").json()
    again = client.post(url, json.dumps({"ops": ops}), content_type="application/json").json()
    assert first["applied"] == 3 and first["skipped"] == 1 and again["applied"] == 3
    assert shoot.shots.count() == 2  # the idea was created once
    shot.refresh_from_db()
    plan.refresh_from_db()
    assert shot.status == "captured" and shot.notes == "Use take 3" and shot.captured_by == studio["freelancer"]
    assert plan.stage == ContentPlan.Stage.IN_PRODUCTION
    unplanned = shoot.shots.get(pk=idea_id)
    assert unplanned.is_unplanned and unplanned.status == "captured"
    assert AuditEvent.objects.filter(verb="shoot.shot_captured", target_id=str(plan.post.pk)).count() == 1


def test_on_set_page_works_offline_and_carries_the_source_link(client, studio):
    shoot = _shoot(studio["restaurant"])
    client.force_login(studio["editor"])
    response = client.get(
        reverse("shoots:on_set", kwargs={"workspace_id": studio["restaurant"].id, "shoot_id": shoot.id})
    )
    body = response.content.decode()
    assert response.status_code == 200 and "onset.js" in body and "data-source-code-link" in body
    sw = client.get(reverse("shoots:sw"))
    assert sw.status_code == 200 and sw["Service-Worker-Allowed"] == "/shoots/"


# ── Uploads and matching ─────────────────────────────────────────────────────


def _asset(ws, name="photo.png"):
    from apps.media_library.models import MediaAsset

    return MediaAsset.objects.create(
        organization=ws.organization,
        workspace=ws,
        file=SimpleUploadedFile(name, PNG),
        filename=name,
        media_type="image",
        mime_type="image/png",
    )


def test_file_names_match_shots_by_code():
    shots = [Shot(code="S01"), Shot(code="S03"), Shot(code="S12")]
    assert services.match_code("S03_tacos.jpg", shots).code == "S03"
    assert services.match_code("casa-s3.mov", shots).code == "S03"
    assert services.match_code("IMG_S12-final.png", shots).code == "S12"
    assert services.match_code("SONY0001.jpg", shots) is None
    assert services.match_code("S99.jpg", shots) is None


def test_matched_material_is_captured_and_attached_to_the_post(studio):
    shoot = _shoot(studio["restaurant"])
    plan = _plan(studio, "Seafood platter")
    shot = services.add_shot(shoot, description="Platter overhead", post=plan.post)
    asset = _asset(studio["restaurant"], "S01_platter.png")
    services.attach_to_shot(shot, asset, studio["editor"])
    shot.refresh_from_db()
    assert shot.status == "captured" and list(shot.media.all()) == [asset]
    assert PostMedia.objects.filter(post=plan.post, media_asset=asset).exists()
    assert AuditEvent.objects.filter(verb="shoot.media_matched", target_id=str(plan.post.pk)).exists()


def test_upload_view_scans_stores_and_matches(client, studio):
    shoot = _shoot(studio["restaurant"])
    services.add_shot(shoot, description="Plating", post=_plan(studio, "Plating").post)
    client.force_login(studio["editor"])
    url = reverse("shoots:upload", kwargs={"workspace_id": studio["restaurant"].id, "shoot_id": shoot.id})
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    response = client.post(
        url,
        {
            "files": [
                SimpleUploadedFile("S01_plating.png", PNG, content_type="image/png"),
                SimpleUploadedFile("random.png", PNG, content_type="image/png"),
                SimpleUploadedFile("S02.mp4", eicar, content_type="video/mp4"),
            ]
        },
    )
    assert response.status_code == 302
    shot = shoot.shots.get(code="S01")
    assert shot.status == "captured" and shot.media.count() == 1
    from apps.media_library.models import MediaAsset

    names = set(MediaAsset.objects.filter(workspace=studio["restaurant"]).values_list("filename", flat=True))
    assert "S01_plating.png" in names and "random.png" in names and "S02.mp4" not in names
    assert MediaAsset.objects.get(filename="random.png").rights.shoot == shoot


# ── Follow-ups and runway ────────────────────────────────────────────────────


def test_missing_must_have_shots_become_follow_up_tasks(studio):
    shoot = _shoot(studio["restaurant"])
    services.add_shot(shoot, description="Must one")
    services.add_shot(shoot, description="Nice one", priority=Shot.Priority.NICE)
    done = services.add_shot(shoot, description="Done one")
    services.mark_shot(done, "captured", studio["editor"])
    tasks = services.create_followups(shoot, studio["editor"])
    assert [t.title for t in tasks] == ["Missing shot S01: Must one"]
    assert services.create_followups(shoot, studio["editor"]) == []  # no duplicates
    shoot.refresh_from_db()
    assert shoot.project is not None and tasks[0].project == shoot.project


def test_runway_counts_covered_posts_and_when_the_next_shoot_is_needed(studio):
    covered = _plan(studio, "Covered", offset=8)
    covered.post.media_attachments.create(media_asset=_asset(studio["restaurant"]), position=0)
    _plan(studio, "Uncovered", offset=12)
    result = services.runway(studio["restaurant"])
    assert result["covered"] == 1 and result["uncovered"] == 1
    assert result["needed_by"] == _day(12) - timedelta(days=5) and result["late"]  # no shoot booked
    _shoot(studio["restaurant"], offset=3)
    assert not services.runway(studio["restaurant"])["late"]


# ── Call sheets ──────────────────────────────────────────────────────────────


def test_public_call_sheet_needs_sharing_and_never_shows_internal_notes(client, studio):
    contact = Contact.objects.create(workspace=studio["restaurant"], name="Ana Pérez", email="ana@casamaya.test")
    shoot = _shoot(
        studio["restaurant"],
        client_contact=contact,
        notes="INTERNAL: client is late paying",
        client_notes="Park behind the kitchen",
    )
    services.add_shot(shoot, description="Plating", post=_plan(studio, "Plating").post)
    url = reverse("shoots_public:call_sheet", kwargs={"token": shoot.share_token})
    assert client.get(url).status_code == 404  # not shared yet
    shoot.share_enabled = True
    shoot.save()
    response = client.get(url)
    body = response.content.decode()
    assert response.status_code == 200 and response["X-Robots-Tag"] == "noindex"
    assert "Park behind the kitchen" in body and "Plating" in body
    assert "INTERNAL" not in body and "late paying" not in body
    assert "data-source-code-link" in body

    pdf = client.get(reverse("shoots_public:call_sheet_pdf", kwargs={"token": shoot.share_token}))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert b"late paying" not in pdf.content


def test_call_sheet_pdf_copes_with_non_latin_text(studio):
    from apps.shoots.callsheet import render_pdf

    shoot = _shoot(studio["restaurant"], title="Café “Señor” 🌮", locations=["Año Nuevo — patio"])
    assert render_pdf(shoot, studio_name="Studio", contact_line="Studio").startswith(b"%PDF")


# ── Scope ────────────────────────────────────────────────────────────────────


def test_freelancers_see_only_the_shoots_they_are_crew_on(client, studio):
    mine = _shoot(studio["restaurant"], "Mine")
    mine.crew.add(studio["freelancer"])
    other = _shoot(studio["restaurant"], "Not mine", offset=6)
    client.force_login(studio["freelancer"])
    ws = studio["restaurant"].id
    assert client.get(reverse("shoots:detail", kwargs={"workspace_id": ws, "shoot_id": mine.id})).status_code == 200
    assert client.get(reverse("shoots:detail", kwargs={"workspace_id": ws, "shoot_id": other.id})).status_code == 404
    body = client.get(reverse("shoots:all")).content.decode()
    assert "Mine" in body and "Not mine" not in body
    sync = reverse("shoots:sync", kwargs={"workspace_id": ws, "shoot_id": other.id})
    assert client.post(sync, json.dumps({"ops": []}), content_type="application/json").status_code == 404
    # Crew can tick shots but not plan the day.
    add = reverse("shoots:shot_add", kwargs={"workspace_id": ws, "shoot_id": mine.id})
    assert client.post(add, {"description": "Sneaky"}).status_code == 403


def test_cross_client_shoot_access_is_refused(client, studio):
    luma = _shoot(studio["skincare"], "Luma flat-lays", notes="Luma internal")
    client.force_login(studio["editor"])  # Casa Maya only
    via_casa = reverse("shoots:detail", kwargs={"workspace_id": studio["restaurant"].id, "shoot_id": luma.id})
    assert client.get(via_casa).status_code == 404
    direct = reverse("shoots:detail", kwargs={"workspace_id": studio["skincare"].id, "shoot_id": luma.id})
    assert client.get(direct).status_code in (302, 403, 404)
    sheet = reverse("shoots:call_sheet", kwargs={"workspace_id": studio["restaurant"].id, "shoot_id": luma.id})
    assert client.get(sheet).status_code == 404
    assert "Luma flat-lays" not in client.get(reverse("shoots:all")).content.decode()


def test_client_users_cannot_open_studio_shoot_pages(client, studio):
    shoot = _shoot(studio["restaurant"])
    client.force_login(studio["restaurant_client"])
    ws = studio["restaurant"].id
    for url in (
        reverse("shoots:list", kwargs={"workspace_id": ws}),
        reverse("shoots:detail", kwargs={"workspace_id": ws, "shoot_id": shoot.id}),
        reverse("shoots:on_set", kwargs={"workspace_id": ws, "shoot_id": shoot.id}),
    ):
        assert client.get(url).status_code in (302, 403, 404), url


# ── Today ────────────────────────────────────────────────────────────────────


def test_today_shows_upcoming_shoot_days_and_posts_without_a_shoot(client, studio):
    shoot = _shoot(studio["restaurant"], "Brunch shoot", offset=3)
    _plan(studio, "Needs a shoot", offset=9, stage=ContentPlan.Stage.SHOT_NEEDED)
    client.force_login(studio["owner"])
    body = client.get(reverse("studio:today")).content.decode()
    assert "Brunch shoot" in body and "Missing:" in body and "No crew" in body
    assert "Needs a shoot" in body
    shoot.crew.add(studio["freelancer"])
    client.force_login(studio["freelancer"])
    assert "Brunch shoot" in client.get(reverse("studio:today")).content.decode()


def test_conflicts_never_name_a_client_the_reader_cannot_see(client, studio):
    """Double-booking is checked studio-wide, but another client's shoot stays anonymous to staff outside it."""
    casa = _shoot(studio["restaurant"], "Casa shoot", equipment=["Sony FX3"])
    casa.crew.add(studio["freelancer"])
    luma = _shoot(studio["skincare"], "Secret Luma launch", call=time(10), wrap=time(12), equipment=["Sony FX3"])
    assert any(
        "Secret Luma launch" in c for c in services.conflicts(casa, {studio["restaurant"].id, luma.workspace_id})
    )
    masked = services.conflicts(casa, {studio["restaurant"].id})
    assert masked and all("Secret Luma" not in c and "Luma Skincare" not in c for c in masked)
    assert any("another client's shoot (10:00–12:00)" in c for c in masked)

    client.force_login(studio["freelancer"])  # crew on Casa Maya only
    ws = studio["restaurant"].id
    for url in (reverse("shoots:detail", kwargs={"workspace_id": ws, "shoot_id": casa.id}), reverse("shoots:all")):
        body = client.get(url).content.decode()
        assert "another client" in body and "Secret Luma" not in body and "Luma Skincare" not in body, url
    client.force_login(studio["owner"])  # on both clients: sees the details
    body = client.get(reverse("shoots:detail", kwargs={"workspace_id": ws, "shoot_id": casa.id})).content.decode()
    assert "Secret Luma launch" in body
