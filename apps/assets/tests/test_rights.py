"""Usage rights: recorded and audited, where each asset is used, expiry warnings, and scope."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.assets.models import AssetRights
from apps.audit.models import AuditEvent
from apps.composer.models import PostMedia
from apps.shoots.models import Shoot
from apps.shoots.services import add_shot, attach_to_shot
from apps.studio.tests.helpers import make_account, make_post, make_studio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    people["account"] = make_account(people["restaurant"], "Casa Maya")
    return people


def _asset(ws, name="rosa.png"):
    from apps.media_library.models import MediaAsset

    return MediaAsset.objects.create(
        organization=ws.organization,
        workspace=ws,
        file=SimpleUploadedFile(name, PNG),
        filename=name,
        media_type="image",
        mime_type="image/png",
    )


def _url(ws, asset):
    return reverse("assets:rights", kwargs={"workspace_id": ws.id, "asset_id": asset.id})


def test_rights_are_saved_and_audited(client, studio):
    ws = studio["restaurant"]
    asset = _asset(ws)
    client.force_login(studio["editor"])
    expires = timezone.localdate() + timedelta(days=30)
    response = client.post(
        _url(ws, asset),
        {
            "source": "licensed",
            "usage": "organic",
            "licence": "12 months, Instagram and Facebook",
            "talent_release": "on",
            "release_reference": "RF-2026-14",
            "expires_on": expires.isoformat(),
        },
    )
    assert response.status_code == 302
    rights = AssetRights.objects.get(media_asset=asset)
    assert rights.usage == "organic" and rights.expires_on == expires and rights.updated_by == studio["editor"]
    assert AuditEvent.objects.filter(verb="asset.rights_changed", target_id=str(asset.pk)).exists()


def test_where_used_and_posts_going_out_after_the_rights_end(client, studio):
    ws = studio["restaurant"]
    asset = _asset(ws)
    late = make_post(ws, studio["account"], when=timezone.now() + timedelta(days=20), title="After expiry")
    early = make_post(ws, studio["account"], when=timezone.now() + timedelta(days=2), title="Before expiry")
    for post in (late, early):
        PostMedia.objects.create(post=post, media_asset=asset, position=0)
    shoot = Shoot.objects.create(workspace=ws, title="Menu shoot", date=timezone.localdate(), call_time="09:00")
    attach_to_shot(add_shot(shoot, description="Rosa plating"), asset)
    AssetRights.objects.create(media_asset=asset, expires_on=timezone.localdate() + timedelta(days=10))
    client.force_login(studio["viewer"])
    response = client.get(_url(ws, asset))
    assert response.status_code == 200
    usage = response.context["usage"]
    assert {r["post"].title for r in usage["posts"]} == {"After expiry", "Before expiry"}
    assert [s.shoot.title for s in usage["shots"]] == ["Menu shoot"]
    assert [p.title for p in response.context["at_risk"]] == ["After expiry"]


def test_only_people_who_edit_media_change_rights(client, studio):
    ws = studio["restaurant"]
    asset = _asset(ws)
    client.force_login(studio["viewer"])
    assert client.get(_url(ws, asset)).status_code == 200
    assert client.post(_url(ws, asset), {"source": "stock", "usage": "any"}).status_code == 403
    assert not AssetRights.objects.filter(media_asset=asset).exists()


def test_cross_client_rights_access_is_refused(client, studio):
    luma_asset = _asset(studio["skincare"], "serum.png")
    client.force_login(studio["editor"])  # Casa Maya only
    assert client.get(_url(studio["restaurant"], luma_asset)).status_code == 404
    assert client.post(_url(studio["restaurant"], luma_asset), {"source": "stock", "usage": "any"}).status_code == 404
    assert client.get(_url(studio["skincare"], luma_asset)).status_code in (302, 403, 404)
    assert not AssetRights.objects.filter(media_asset=luma_asset).exists()


def test_client_users_never_see_rights(client, studio):
    ws = studio["restaurant"]
    asset = _asset(ws)
    client.force_login(studio["restaurant_client"])
    assert client.get(_url(ws, asset)).status_code in (302, 403, 404)


def test_media_library_links_to_rights(client, studio):
    ws = studio["restaurant"]
    asset = _asset(ws)
    client.force_login(studio["editor"])
    detail = reverse("media_library:asset_detail", kwargs={"workspace_id": ws.id, "asset_id": asset.id})
    assert _url(ws, asset) in client.get(detail, HTTP_HX_REQUEST="true").content.decode()
