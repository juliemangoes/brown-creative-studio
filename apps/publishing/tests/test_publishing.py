"""Publishing: posts go out once, TikTok is posted by hand and tracked to completion,
uncertain failures need a check before retrying, pre-publish checks alert once, and no
client (or other client's staff) reaches any of it."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.composer.models import PlatformPost, PostMedia
from apps.notifications.models import Notification
from apps.publisher.engine import PublishEngine
from apps.publisher.models import PublishLog
from apps.publishing import checks, services
from apps.publishing.models import ManualPublishTask, PublishAlert
from apps.studio.tests.helpers import join, make_account, make_post, make_studio, make_user
from providers.exceptions import PublishError

# The engine publishes from worker threads, which only see committed rows.
engine = pytest.mark.django_db(transaction=True)

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


@pytest.fixture(autouse=True)
def assisted_tiktok(settings):
    """Production default: TikTok is posted by hand (the test settings turn Direct Post on)."""
    settings.TIKTOK_DIRECT_POST_ENABLED = False


@pytest.fixture
def studio(db):
    people = make_studio()
    people["ig"] = make_account(people["restaurant"], "Casa Maya IG", "instagram")
    people["tiktok"] = make_account(people["restaurant"], "Casa Maya TikTok", "tiktok")
    people["luma_tiktok"] = make_account(people["skincare"], "Luma TikTok", "tiktok")
    return people


def _video(ws, post, name="clip.mp4"):
    from apps.media_library.models import MediaAsset

    asset = MediaAsset.objects.create(
        organization=ws.organization,
        workspace=ws,
        file=SimpleUploadedFile(name, MP4),
        filename=name,
        media_type="video",
        mime_type="video/mp4",
        processing_status="completed",
    )
    PostMedia.objects.create(post=post, media_asset=asset)
    return asset


def _scheduled(studio, account, *, minutes=0, title="Taco Tuesday", ws=None, author=None):
    ws = ws or studio["restaurant"]
    post = make_post(
        ws,
        account,
        status="scheduled",
        when=timezone.now() + timedelta(minutes=minutes),
        title=title,
        author=author or studio["editor"],
    )
    _video(ws, post)
    return post.platform_posts.get()


def _ok(url="https://www.instagram.com/p/ABC123/"):
    return {"success": True, "platform_post_id": "1789", "url": url, "response": {}, "async_publish": False}


# ── Platforms ────────────────────────────────────────────────────────────────


def test_only_facebook_instagram_and_tiktok_are_offered(db):
    from apps.social_accounts.models import PlatformVisibility

    assert sorted(v for v, _ in PlatformVisibility.visible_choices()) == ["facebook", "instagram", "tiktok"]


# ── Publishing once ──────────────────────────────────────────────────────────


@engine
def test_an_approved_post_publishes_once_and_keeps_its_live_link(studio):
    pp = _scheduled(studio, studio["ig"])
    with patch.object(PublishEngine, "_dispatch_to_provider", return_value=_ok()) as dispatch:
        PublishEngine().poll_and_publish()
        PublishEngine().poll_and_publish()
    assert dispatch.call_count == 1
    pp.refresh_from_db()
    assert pp.status == "published" and pp.platform_post_id == "1789"
    assert services.live_url(pp) == "https://www.instagram.com/p/ABC123/"
    assert PublishLog.objects.filter(platform_post=pp).count() == 1
    assert pp.post.platform_posts.get().status == "published"  # live link did not reopen approval


@engine
def test_a_timeout_on_instagram_is_not_retried_automatically(studio):
    pp = _scheduled(studio, studio["ig"])
    with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=PublishError("Read timed out")) as dispatch:
        PublishEngine().poll_and_publish()
        PublishEngine().poll_and_publish()
    assert dispatch.call_count == 1
    pp.refresh_from_db()
    assert pp.status == "failed"
    assert pp.platform_extra.get("outcome_unknown") is True
    assert "may or may not be live" in pp.publish_error
    # The owner hears about it, not only the author.
    assert Notification.objects.filter(user=studio["owner"], event_type="post_failed").exists()


@engine
def test_a_clean_refusal_is_retried_by_the_engine_as_before(studio):
    pp = _scheduled(studio, studio["ig"])
    err = PublishError("Bad request")
    err.status_code = 400
    with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=err):
        PublishEngine().poll_and_publish()
    pp.refresh_from_db()
    assert pp.status == "scheduled" and pp.retry_count == 1
    assert not pp.platform_extra.get("outcome_unknown")


@engine
def test_retrying_an_uncertain_failure_needs_a_check(studio):
    pp = _scheduled(studio, studio["ig"])
    with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=PublishError("Connection reset")):
        PublishEngine().poll_and_publish()
    pp.refresh_from_db()
    with pytest.raises(services.PublishingError, match="confirm the post is not live"):
        services.retry(pp, studio["owner"])
    # No other route gets round it either (composer, calendar, API all save the row).
    pp.transition_to("scheduled")
    with pytest.raises(services.PublishingError, match="may already be live"):
        pp.save()
    pp.refresh_from_db()
    services.retry(pp, studio["owner"], confirmed_not_live=True)
    pp.refresh_from_db()
    assert pp.status == "scheduled" and "outcome_unknown" not in pp.platform_extra
    assert AuditEvent.objects.filter(verb="publishing.retried").exists()


@engine
def test_a_failed_post_found_live_is_marked_published_without_posting_again(studio):
    pp = _scheduled(studio, studio["ig"])
    with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=PublishError("Read timed out")):
        PublishEngine().poll_and_publish()
    pp.refresh_from_db()
    services.mark_live(pp, "https://www.instagram.com/p/XYZ987/", studio["owner"])
    pp.refresh_from_db()
    assert pp.status == "published" and pp.platform_post_id == "XYZ987"
    with patch.object(PublishEngine, "_dispatch_to_provider") as dispatch:
        PublishEngine().poll_and_publish()
    dispatch.assert_not_called()
    with pytest.raises(services.PublishingError):
        services.retry(pp, studio["owner"], confirmed_not_live=True)


def test_retry_refuses_a_disconnected_account(studio):
    pp = _scheduled(studio, studio["ig"])
    PlatformPost.objects.filter(pk=pp.pk).update(status="failed")
    studio["ig"].connection_status = "disconnected"
    studio["ig"].save()
    pp.refresh_from_db()
    with pytest.raises(services.PublishingError, match="Reconnect"):
        services.retry(pp, studio["owner"])


# ── TikTok assisted posting ──────────────────────────────────────────────────


@engine
def test_tiktok_is_never_sent_to_the_api_and_becomes_manual_action(studio, django_capture_on_commit_callbacks):
    pp = _scheduled(studio, studio["tiktok"], minutes=30)
    with (
        patch.object(PublishEngine, "_dispatch_to_provider") as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        PublishEngine().poll_and_publish()
        PublishEngine().poll_and_publish()
    dispatch.assert_not_called()
    pp.refresh_from_db()
    assert pp.status == "manual_action"
    task = ManualPublishTask.objects.get(platform_post=pp)
    assert task.status == "open" and task.assignee == studio["editor"]
    assert Notification.objects.filter(user=studio["editor"], event_type="manual_publish").count() == 1
    assert AuditEvent.objects.filter(verb="publishing.manual_needed").count() == 1


@engine
def test_manual_posts_wait_until_the_lead_time(studio):
    pp = _scheduled(studio, studio["tiktok"], minutes=180)
    PublishEngine().poll_and_publish()
    pp.refresh_from_db()
    assert pp.status == "scheduled" and not ManualPublishTask.objects.exists()


@engine
def test_direct_post_switched_on_uses_the_api(studio, settings):
    settings.TIKTOK_DIRECT_POST_ENABLED = True
    pp = _scheduled(studio, studio["tiktok"])
    with patch.object(PublishEngine, "_dispatch_to_provider", return_value=_ok("https://www.tiktok.com/@x/video/1")):
        PublishEngine().poll_and_publish()
    pp.refresh_from_db()
    assert pp.status == "published" and not ManualPublishTask.objects.exists()


def test_pasting_the_live_link_publishes_once(studio):
    pp = _scheduled(studio, studio["tiktok"], minutes=10)
    services.route_due_manual_posts()
    task = ManualPublishTask.objects.get(platform_post=pp)
    with pytest.raises(services.PublishingError, match="isn't a Tiktok link"):
        services.complete_manual(task, "https://www.instagram.com/p/1/", studio["editor"])
    services.complete_manual(task, "https://www.tiktok.com/@casamaya/video/7301234567890", studio["editor"])
    pp.refresh_from_db()
    task.refresh_from_db()
    assert pp.status == "published" and pp.published_at is not None
    assert pp.platform_post_id == "7301234567890"
    assert services.live_url(pp).endswith("/video/7301234567890")
    assert task.status == "done" and task.completed_by == studio["editor"]
    with pytest.raises(services.PublishingError, match="already"):
        services.complete_manual(task, "https://www.tiktok.com/@casamaya/video/7301234567890", studio["owner"])
    assert PublishLog.objects.filter(platform_post=pp).count() == 1
    assert AuditEvent.objects.filter(verb="publishing.manual_done").count() == 1


def test_routing_twice_opens_one_task(studio):
    pp = _scheduled(studio, studio["tiktok"], minutes=5)
    services.to_manual(pp.pk, "direct_post_off", from_statuses=("scheduled",))
    assert services.to_manual(pp.pk, "direct_post_off", from_statuses=("scheduled",)) is None
    assert ManualPublishTask.objects.filter(platform_post=pp).count() == 1


def test_a_tiktok_inbox_result_becomes_manual_action_not_failed(studio):
    from providers.types import PublishState

    pp = _scheduled(studio, studio["tiktok"])
    PlatformPost.objects.filter(pk=pp.pk).update(status="publishing", platform_post_id="v_inbox_1")
    pp.refresh_from_db()

    class Status:
        state = PublishState.INBOX
        error = "TikTok sent this video to the creator's inbox."
        platform_post_id = ""

    class Provider:
        publish_is_async = True

        def check_publish_status(self, token, handle):
            return Status()

    now = timezone.now()
    with patch("apps.publisher.engine._provider_and_access_token", return_value=(Provider(), "tok")):
        settled = PublishEngine()._confirm_one_publish(
            pp, stale_before=now, confirm_before=now - timedelta(hours=1), unconfirmed_before=now
        )
    assert settled
    pp.refresh_from_db()
    assert pp.status == "manual_action"
    assert ManualPublishTask.objects.get(platform_post=pp).reason == "inbox"


def test_a_reopened_post_cancels_its_manual_task(studio):
    pp = _scheduled(studio, studio["tiktok"], minutes=5)
    services.route_due_manual_posts()
    task = ManualPublishTask.objects.get(platform_post=pp)
    PlatformPost.objects.filter(pk=pp.pk).update(status="pending_review")
    assert services.close_if_stale(task)
    task.refresh_from_db()
    assert task.status == "cancelled"
    with pytest.raises(services.PublishingError):
        services.complete_manual(task, "https://www.tiktok.com/@x/video/1", studio["editor"])


def test_late_manual_posts_get_one_reminder(studio):
    pp = _scheduled(studio, studio["tiktok"], minutes=5)
    services.route_due_manual_posts()
    ManualPublishTask.objects.filter(platform_post=pp).update(due_at=timezone.now() - timedelta(hours=2))
    assert services.send_reminders() == 1
    assert services.send_reminders() == 0


def test_assignee_falls_back_to_the_owner(studio):
    outsider = make_user("gone@studio.test", studio["org"])
    pp = _scheduled(studio, studio["tiktok"], minutes=5, author=outsider)
    services.route_due_manual_posts()
    assert ManualPublishTask.objects.get(platform_post=pp).assignee == studio["owner"]


# ── Pre-publish checks ───────────────────────────────────────────────────────


def test_checks_find_connection_approval_and_media_problems(studio):
    ws = studio["restaurant"]
    ws.approval_workflow_mode = "required_internal"
    ws.save()
    studio["ig"].connection_status = "disconnected"
    studio["ig"].save()
    post = make_post(ws, studio["ig"], status="pending_review", when=timezone.now() + timedelta(hours=5))
    no_video = make_post(ws, studio["tiktok"], status="approved", when=timezone.now() + timedelta(hours=6))
    rows = {pp.id: [i.code for i in checks.check(pp)] for pp in checks.upcoming_posts([ws.id])}
    ig_codes = rows[post.platform_posts.get().id]
    assert "disconnected" in ig_codes and "not_approved" in ig_codes
    tt_codes = rows[no_video.platform_posts.get().id]
    assert "media" in tt_codes and "not_scheduled" in tt_codes and "manual" in tt_codes


def test_alerts_go_out_once_per_problem(studio):
    studio["ig"].connection_status = "disconnected"
    studio["ig"].save()
    pp = _scheduled(studio, studio["ig"], minutes=120)
    assert checks.run_checks() == 1
    assert checks.run_checks() == 0
    assert PublishAlert.objects.filter(platform_post=pp, code="disconnected").count() == 1
    assert Notification.objects.filter(user=studio["editor"], event_type="publish_check").count() == 1
    assert Notification.objects.filter(user=studio["owner"], event_type="publish_check").count() == 1


def test_posts_beyond_the_alert_window_are_not_alerted_yet(studio):
    studio["ig"].connection_status = "disconnected"
    studio["ig"].save()
    _scheduled(studio, studio["ig"], minutes=60 * 30)
    assert checks.run_checks() == 0


# ── Pages, permissions and scope ─────────────────────────────────────────────


def _task(studio, account=None, ws=None, minutes=5):
    pp = _scheduled(studio, account or studio["tiktok"], minutes=minutes, ws=ws)
    services.route_due_manual_posts()
    return ManualPublishTask.objects.get(platform_post=pp)


def test_queue_pages_show_each_group(studio, client):
    _task(studio)
    failed = make_post(studio["restaurant"], studio["ig"], status="failed", title="Broken reel")
    PlatformPost.objects.filter(post=failed).update(publish_error="Instagram refused the video.")
    client.force_login(studio["owner"])
    page = client.get(reverse("publishing:client", kwargs={"workspace_id": studio["restaurant"].id}))
    assert page.status_code == 200
    body = page.content.decode()
    for label in (
        "Manual action needed",
        "Failed",
        "Publishing",
        "Scheduled",
        "Published",
        "Instagram refused the video.",
    ):
        assert label in body
    assert client.get(reverse("publishing:queue")).status_code == 200


def test_the_manual_page_offers_the_video_caption_and_link_form(studio, client):
    task = _task(studio)
    client.force_login(studio["editor"])
    url = reverse("publishing:task", kwargs={"workspace_id": studio["restaurant"].id, "task_id": task.id})
    page = client.get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Taco Tuesday caption" in body and "Copy caption" in body and "Mark published" in body
    attachment = task.platform_post.post.media_attachments.get()
    download = client.get(
        reverse(
            "publishing:task_download",
            kwargs={"workspace_id": studio["restaurant"].id, "task_id": task.id, "attachment_id": attachment.id},
        )
    )
    assert download.status_code == 200 and "attachment" in download["Content-Disposition"]
    done = client.post(
        reverse("publishing:task_complete", kwargs={"workspace_id": studio["restaurant"].id, "task_id": task.id}),
        {"live_url": "https://www.tiktok.com/@casamaya/video/123"},
    )
    assert done.status_code == 302
    task.refresh_from_db()
    assert task.status == "done"


def test_only_the_assignee_or_a_publisher_completes(studio, client):
    task = _task(studio)
    client.force_login(studio["viewer"])
    resp = client.post(
        reverse("publishing:task_complete", kwargs={"workspace_id": studio["restaurant"].id, "task_id": task.id}),
        {"live_url": "https://www.tiktok.com/@casamaya/video/123"},
    )
    assert resp.status_code == 403
    task.refresh_from_db()
    assert task.status == "open"


def test_clients_never_reach_publishing(studio, client):
    task = _task(studio)
    client.force_login(studio["restaurant_client"])
    ws = studio["restaurant"].id
    for url in (
        reverse("publishing:client", kwargs={"workspace_id": ws}),
        reverse("publishing:task", kwargs={"workspace_id": ws, "task_id": task.id}),
        reverse("publishing:queue"),
    ):
        assert client.get(url).status_code in (302, 403, 404)


def test_another_clients_task_is_not_reachable_by_changing_the_url(studio, client):
    luma_task = _task(studio, studio["luma_tiktok"], ws=studio["skincare"])
    manager = studio["manager"]  # works on the restaurant only
    client.force_login(manager)
    restaurant = studio["restaurant"].id
    assert (
        client.get(reverse("publishing:task", kwargs={"workspace_id": restaurant, "task_id": luma_task.id})).status_code
        == 404
    )
    assert (
        client.post(
            reverse("publishing:task_complete", kwargs={"workspace_id": restaurant, "task_id": luma_task.id}),
            {"live_url": "https://www.tiktok.com/@luma/video/1"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            reverse(
                "publishing:retry",
                kwargs={"workspace_id": restaurant, "platform_post_id": luma_task.platform_post_id},
            )
        ).status_code
        == 404
    )
    skincare = studio["skincare"].id
    assert client.get(reverse("publishing:client", kwargs={"workspace_id": skincare})).status_code in (403, 404)
    # The all-clients queue lists only the manager's own clients.
    body = client.get(reverse("publishing:queue")).content.decode()
    assert "Luma" not in body
    luma_task.refresh_from_db()
    assert luma_task.status == "open"


def test_retry_and_mark_live_need_publish_permission(studio, client):
    post = make_post(studio["restaurant"], studio["ig"], status="failed")
    pp = post.platform_posts.get()
    client.force_login(studio["viewer"])
    ws = studio["restaurant"].id
    assert (
        client.post(reverse("publishing:retry", kwargs={"workspace_id": ws, "platform_post_id": pp.id})).status_code
        == 403
    )
    assert (
        client.post(
            reverse("publishing:mark_live", kwargs={"workspace_id": ws, "platform_post_id": pp.id}),
            {"live_url": "https://www.instagram.com/p/1/"},
        ).status_code
        == 403
    )


def test_today_shows_manual_posts(studio, client):
    _task(studio)
    join(make_user("x@studio.test", studio["org"]), studio["skincare"], "editor")
    client.force_login(studio["owner"])
    body = client.get(reverse("studio:today")).content.decode()
    assert "Post by hand" in body


# ── Instagram's live publishing limit ───────────────────────────────────────


def _limit_provider(payload):
    from unittest.mock import MagicMock

    from providers.instagram import InstagramProvider

    provider = InstagramProvider({"app_id": "id", "app_secret": "secret"})
    provider._request = MagicMock(return_value=MagicMock(json=MagicMock(return_value=payload)))
    return provider


def test_instagram_limit_stops_before_publishing_when_used_up():
    from providers.exceptions import QuotaExceededError
    from providers.types import PublishContent

    content = PublishContent(text="hi", extra={"ig_user_id": "ig-1"})
    full = _limit_provider({"data": [{"config": {"quota_total": 50}, "quota_usage": 50}]})
    with pytest.raises(QuotaExceededError):
        full.check_publishing_limit("tok", content)
    assert full._request.call_args.args[1].endswith("/ig-1/content_publishing_limit")
    _limit_provider({"data": [{"config": {"quota_total": 50}, "quota_usage": 3}]}).check_publishing_limit(
        "tok", content
    )
    broken = _limit_provider({})
    broken._request.side_effect = RuntimeError("down")
    broken.check_publishing_limit("tok", content)  # a failed check never blocks publishing
