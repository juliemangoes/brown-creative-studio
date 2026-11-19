"""Approval gaps closed: exact versions, reopening on any change, every route into scheduled, the client stage."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.approvals import services as approvals
from apps.audit.models import AuditEvent
from apps.composer.models import PlatformPost, PostMedia
from apps.composer.services import create_post, transition_platform_post
from apps.reviews.models import ApprovalRecord, ApprovalRule, CommentPin
from apps.reviews.services import ApprovalRequiredError, clearance, override_client_approval
from apps.studio.tests.helpers import join, make_account, make_post, make_studio, make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    for ws in (people["restaurant"], people["skincare"]):
        ws.approval_workflow_mode = "required_internal_and_client"
        ws.save(update_fields=["approval_workflow_mode"])
    people["account"] = make_account(people["restaurant"], "Casa Maya", platform="linkedin_personal")
    return people


def _draft(studio, title="Brunch"):
    return make_post(
        studio["restaurant"], studio["account"], status="draft", when=timezone.now() + timedelta(days=3), title=title
    )


def _through_client(studio, post):
    ws = studio["restaurant"]
    approvals.submit_for_review(post, studio["editor"], ws)
    approvals.approve_post(post, studio["owner"], ws)
    approvals.approve_post(post, studio["restaurant_client"], ws)
    return post.platform_posts.get()


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


def _portal(client, user, ws):
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(ws.id)
    session.save()


# ── Records: who approved which exact version ────────────────────────────────


def test_each_decision_records_the_exact_version(studio):
    post = _draft(studio)
    pp = _through_client(studio, post)
    records = list(ApprovalRecord.objects.filter(post=post).order_by("created_at"))
    assert [(r.stage, r.decision) for r in records] == [
        ("internal", "submitted"),
        ("internal", "approved"),
        ("client", "approved"),
    ]
    client_record = records[-1]
    assert client_record.actor == studio["restaurant_client"] and client_record.version is not None
    assert client_record.version.snapshot["caption"] == "Brunch caption"
    assert str(pp.id) in client_record.fingerprints
    assert clearance(pp)["cleared"]


# ── The client stage ─────────────────────────────────────────────────────────


def test_staff_cannot_approve_the_client_stage(client, studio):
    post = _draft(studio)
    approvals.submit_for_review(post, studio["editor"], studio["restaurant"])
    approvals.approve_post(post, studio["owner"], studio["restaurant"])
    pp = post.platform_posts.get()
    assert pp.status == "pending_client"
    with pytest.raises(ValueError, match="client"):
        approvals.approve_post(post, studio["owner"], studio["restaurant"])
    client.force_login(studio["owner"])
    url = reverse(
        "composer:transition_platform_post",
        kwargs={"workspace_id": studio["restaurant"].id, "post_id": post.id, "platform_post_id": pp.id},
    )
    response = client.post(url, {"target_status": "approved"})
    assert response.status_code == 400 and "client" in response.json()["error"]
    pp.refresh_from_db()
    assert pp.status == "pending_client"


def test_client_cannot_approve_versions_still_with_the_studio(studio):
    other = make_account(studio["restaurant"], "Casa Maya Page", platform="facebook")
    post = _draft(studio)
    PlatformPost.objects.create(post=post, social_account=other, status="draft")
    approvals.submit_for_review(post, studio["editor"], studio["restaurant"])
    ig = post.platform_posts.get(social_account=studio["account"])
    approvals.approve_post(ig, studio["owner"], studio["restaurant"])  # only Instagram reviewed
    approvals.approve_post(post, studio["restaurant_client"], studio["restaurant"])  # client approves "the post"
    statuses = dict(post.platform_posts.values_list("social_account__platform", "status"))
    assert statuses == {"linkedin_personal": "approved", "facebook": "pending_review"}


def test_override_needs_permission_and_a_reason(studio):
    post = _draft(studio)
    approvals.submit_for_review(post, studio["editor"], studio["restaurant"])
    approvals.approve_post(post, studio["owner"], studio["restaurant"])
    with pytest.raises(PermissionError):
        override_client_approval(post, studio["manager"], "Approved by phone")
    with pytest.raises(ValueError, match="reason"):
        override_client_approval(post, studio["owner"], "  ")
    override_client_approval(post, studio["owner"], "Ana approved by phone")
    pp = post.platform_posts.get()
    assert pp.status == "approved" and clearance(pp)["client"].stage == "override"
    assert AuditEvent.objects.filter(verb="approval.override", data__reason="Ana approved by phone").exists()


def test_approval_rule_limits_who_approves(studio):
    other_client = make_user("luis@casamaya.test", studio["org"])
    join(other_client, studio["restaurant"], "client")
    rule = ApprovalRule.objects.create(workspace=studio["restaurant"])
    rule.client_approvers.set([studio["restaurant_client"]])
    post = _draft(studio)
    approvals.submit_for_review(post, studio["editor"], studio["restaurant"])
    approvals.approve_post(post, studio["owner"], studio["restaurant"])
    with pytest.raises(ValueError, match="approval rule"):
        approvals.approve_post(post, other_client, studio["restaurant"])
    approvals.approve_post(post, studio["restaurant_client"], studio["restaurant"])
    assert post.platform_posts.get().status == "approved"


# ── Every route into scheduled ───────────────────────────────────────────────


@pytest.fixture
def uncleared(studio):
    """An 'approved' row that never went through the client (as a bypass or old data would leave it)."""
    post = _draft(studio, "Unreviewed special")
    PlatformPost.objects.filter(post=post).update(status="approved")
    return post, post.platform_posts.get()


def test_model_save_refuses_unapproved_content(uncleared):
    _, pp = uncleared
    pp.status = "scheduled"
    with pytest.raises(ApprovalRequiredError):
        pp.save()


def test_composer_transition_refuses(client, studio, uncleared):
    post, pp = uncleared
    client.force_login(studio["owner"])
    url = reverse(
        "composer:transition_platform_post",
        kwargs={"workspace_id": studio["restaurant"].id, "post_id": post.id, "platform_post_id": pp.id},
    )
    response = client.post(url, {"target_status": "scheduled"})
    assert response.status_code == 400 and "approval" in response.json()["error"]
    pp.refresh_from_db()
    assert pp.status == "approved"


def test_composer_save_schedule_action_refuses(client, studio, uncleared):
    post, pp = uncleared
    client.force_login(studio["owner"])
    when = timezone.localtime() + timedelta(days=2)
    client.post(
        reverse("composer:save_post_edit", kwargs={"workspace_id": studio["restaurant"].id, "post_id": post.id}),
        {
            "action": "schedule",
            "caption": post.caption,
            "title": post.title,
            "selected_accounts": [str(studio["account"].id)],
            "scheduled_date": when.strftime("%Y-%m-%d"),
            "scheduled_time": when.strftime("%H:%M"),
        },
    )
    pp.refresh_from_db()
    assert pp.status != "scheduled"


def test_calendar_drag_refuses(client, studio, uncleared):
    _, pp = uncleared
    client.force_login(studio["owner"])
    response = client.post(
        reverse("calendar:reschedule", kwargs={"workspace_id": studio["restaurant"].id}),
        {"platform_post_id": str(pp.id), "new_datetime": (timezone.now() + timedelta(days=4)).isoformat()},
    )
    pp.refresh_from_db()
    assert pp.status == "approved"
    assert response.status_code == 400 or pp.status != "scheduled"


def test_calendar_bulk_publish_refuses(client, studio, uncleared):
    _, pp = uncleared
    client.force_login(studio["owner"])
    response = client.post(
        reverse("calendar:bulk_platform_action", kwargs={"workspace_id": studio["restaurant"].id}),
        {"action": "publish", "platform_post_ids": [str(pp.id)]},
    )
    pp.refresh_from_db()
    assert pp.status == "approved", response.content


def test_rest_api_and_mcp_service_paths_refuse(studio, uncleared):
    # The REST /posts and /schedule routes and the MCP schedule_post tool call these services.
    with pytest.raises(ValueError):
        create_post(
            workspace=studio["restaurant"],
            social_account=studio["account"],
            caption="x",
            author=studio["owner"],
            status="scheduled",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
    draft = _draft(studio, "API draft").platform_posts.get()
    with pytest.raises(ValueError):
        transition_platform_post(draft, "scheduled", scheduled_at=timezone.now() + timedelta(days=1))


def test_fully_approved_post_schedules_through_the_composer(client, studio):
    post = _draft(studio)
    pp = _through_client(studio, post)
    client.force_login(studio["owner"])
    url = reverse(
        "composer:transition_platform_post",
        kwargs={"workspace_id": studio["restaurant"].id, "post_id": post.id, "platform_post_id": pp.id},
    )
    response = client.post(url, {"target_status": "scheduled"})
    assert response.status_code == 200, response.content
    pp.refresh_from_db()
    assert pp.status == "scheduled"


# ── Any change after approval reopens it, from any status ────────────────────


@pytest.fixture
def scheduled(studio):
    post = _draft(studio)
    pp = _through_client(studio, post)
    pp.transition_to("scheduled")  # as the composer does once approvals are complete
    pp.scheduled_at = timezone.now() + timedelta(days=2)
    pp.save()
    pp.refresh_from_db()
    assert pp.status == "scheduled"
    return post, pp


def test_caption_edit_reopens_a_scheduled_post(scheduled):
    post, pp = scheduled
    post.caption = "Brunch caption, now with prices"
    post.save(update_fields=["caption", "updated_at"])  # what the REST PATCH route does
    pp.refresh_from_db()
    assert pp.status == "pending_review"
    assert ApprovalRecord.objects.filter(post=post, decision="reopened").exists()
    assert AuditEvent.objects.filter(verb="approval.reopened").exists()
    assert not clearance(pp)["cleared"]


def test_media_change_reopens(studio, scheduled):
    post, pp = scheduled
    PostMedia.objects.create(post=post, media_asset=_asset(studio["restaurant"]), position=0)
    pp.refresh_from_db()
    assert pp.status == "pending_review"


def test_replacing_a_media_file_reopens(studio):
    post = _draft(studio)
    asset = _asset(studio["restaurant"])
    PostMedia.objects.create(post=post, media_asset=asset, position=0)
    pp = _through_client(studio, post)
    assert pp.status == "approved"
    asset.file = SimpleUploadedFile("photo-v2.png", PNG + b"v2")
    asset.save()
    pp.refresh_from_db()
    assert pp.status == "pending_review"


@pytest.mark.parametrize(
    "field, value", [("platform_specific_caption", "IG-only caption"), ("platform_extra", {"location": "San Ignacio"})]
)
def test_platform_override_or_setting_reopens(scheduled, field, value):
    post, pp = scheduled
    setattr(pp, field, value)
    pp.save()
    pp.refresh_from_db()
    assert pp.status == "pending_review"


def test_pending_client_edit_goes_back_to_studio_review(studio):
    post = _draft(studio)
    approvals.submit_for_review(post, studio["editor"], studio["restaurant"])
    approvals.approve_post(post, studio["owner"], studio["restaurant"])
    post.title = "New title"
    post.save()
    assert post.platform_posts.get().status == "pending_review"


def test_notes_and_times_do_not_reopen(scheduled):
    post, pp = scheduled
    post.internal_notes = "Boost this one"
    post.scheduled_at = timezone.now() + timedelta(days=5)
    post.save()
    pp.refresh_from_db()
    assert pp.status == "scheduled"


def test_mode_none_keeps_brightbean_behaviour(studio):
    ws = studio["skincare"]
    ws.approval_workflow_mode = "none"
    ws.save()
    account = make_account(ws, "Luma", platform="linkedin_personal")
    post = make_post(ws, account, status="draft", title="Free")
    pp = post.platform_posts.get()
    transition_platform_post(pp, "scheduled", scheduled_at=timezone.now() + timedelta(days=1))
    post.caption = "changed"
    post.save()
    pp.refresh_from_db()
    assert pp.status == "scheduled"


# ── Portal: week and month review, pins ──────────────────────────────────────


def _to_client(studio, title, days):
    post = make_post(
        studio["restaurant"], studio["account"], status="draft", when=timezone.now() + timedelta(days=days), title=title
    )
    approvals.submit_for_review(post, studio["editor"], studio["restaurant"])
    approvals.approve_post(post, studio["owner"], studio["restaurant"])
    return post


def test_portal_month_review_approves_what_is_shown(client, studio):
    posts = [_to_client(studio, f"Post {i}", d) for i, d in enumerate((1, 2, 3))]
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    page = client.get(reverse("reviews_portal:batch") + "?period=all")
    assert page.status_code == 200 and all(p.caption in page.content.decode() for p in posts)
    foreign = make_post(studio["skincare"], make_account(studio["skincare"], "Luma"), status="draft", title="Luma")
    client.post(
        reverse("reviews_portal:approve_batch"), {"post_ids": [str(p.id) for p in posts[:2]] + [str(foreign.id)]}
    )
    statuses = [p.platform_posts.get().status for p in posts]
    assert statuses == ["approved", "approved", "pending_client"]
    assert foreign.platform_posts.get().status == "draft"


def test_billing_contact_cannot_approve_in_batch(client, studio):
    from apps.members.models import WorkspaceMembership
    from apps.studio.permissions import ensure_client_roles

    roles = ensure_client_roles(studio["org"])
    WorkspaceMembership.objects.filter(user=studio["restaurant_client"]).update(
        custom_role=roles["Client billing contact"]
    )
    post = _to_client(studio, "Post", 1)
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    assert client.post(reverse("reviews_portal:approve_batch"), {"post_ids": [str(post.id)]}).status_code == 403
    assert post.platform_posts.get().status == "pending_client"


def test_pins_on_images_and_video_moments(client, studio):
    post = _to_client(studio, "Pinned", 1)
    asset = _asset(studio["restaurant"])
    from apps.reviews.services import bypass

    with bypass():
        PostMedia.objects.create(post=post, media_asset=asset, position=0)
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    url = reverse("reviews_portal:comment", kwargs={"post_id": post.id})
    client.post(url, {"body": "Brighter here", "pin_asset": str(asset.id), "pin_x": "0.25", "pin_y": "0.6"})
    client.post(url, {"body": "Cut here", "pin_asset": str(asset.id), "pin_t": "12.5"})
    stranger = _asset(studio["skincare"], "other.png")
    client.post(url, {"body": "Sneaky", "pin_asset": str(stranger.id), "pin_x": "0.5", "pin_y": "0.5"})
    pins = list(CommentPin.objects.order_by("comment__created_at"))
    assert [(p.x, p.y, p.timestamp_seconds) for p in pins] == [(0.25, 0.6, None), (None, None, 12.5)]
    assert pins[1].label() == "at 0:12"


# ── Portal links ─────────────────────────────────────────────────────────────


def test_studio_revokes_portal_links(client, studio):
    from apps.client_portal.models import MagicLinkToken

    token = MagicLinkToken.objects.create(user=studio["restaurant_client"], workspace=studio["restaurant"])
    other = MagicLinkToken.objects.create(user=studio["skincare_client"], workspace=studio["skincare"])
    client.force_login(studio["owner"])
    page = client.get(reverse("reviews:portal_links", kwargs={"workspace_id": studio["restaurant"].id}))
    assert page.status_code == 200 and "ana@casamaya.test" in page.content.decode()
    assert (
        client.post(
            reverse("reviews:revoke_link", kwargs={"workspace_id": studio["restaurant"].id, "token_id": other.id})
        ).status_code
        == 404
    )
    client.post(reverse("reviews:revoke_link", kwargs={"workspace_id": studio["restaurant"].id, "token_id": token.id}))
    token.refresh_from_db()
    assert token.expires_at <= timezone.now()
    other.refresh_from_db()
    assert other.expires_at > timezone.now()
    client.force_login(studio["restaurant_client"])
    assert (
        client.get(reverse("reviews:portal_links", kwargs={"workspace_id": studio["restaurant"].id})).status_code == 403
    )


# ── History ──────────────────────────────────────────────────────────────────


def test_history_page_shows_the_whole_story(client, studio):
    from apps.approvals.models import PostComment

    post = _draft(studio)
    PostComment.objects.create(post=post, author=studio["owner"], body="Margin note", visibility="internal")
    _through_client(studio, post)
    client.force_login(studio["editor"])
    body = client.get(
        reverse("reviews:post", kwargs={"workspace_id": studio["restaurant"].id, "post_id": post.id})
    ).content.decode()
    for text in (
        "Post created",
        "Studio review: submitted",
        "Studio review: approved",
        "Client approval: approved",
        "Cleared to schedule",
        "Margin note",
    ):
        assert text in body
    from apps.reviews.history import post_history

    portal_view = [e.title for e in post_history(post, include_internal=False)]
    assert not any("Margin" in (e.detail or "") for e in post_history(post, include_internal=False))
    assert "Client approval: approved · version 1" in " ".join(portal_view) or any(
        "Client approval" in t for t in portal_view
    )
    client.force_login(studio["restaurant_client"])
    assert (
        client.get(
            reverse("reviews:post", kwargs={"workspace_id": studio["restaurant"].id, "post_id": post.id})
        ).status_code
        == 403
    )


def test_new_workspaces_require_studio_then_client_approval(client, studio):
    client.force_login(studio["owner"])
    client.post(reverse("workspaces:create"), {"name": "Brand new client"})
    from apps.workspaces.models import Workspace

    assert Workspace.objects.get(name="Brand new client").approval_workflow_mode == "required_internal_and_client"
