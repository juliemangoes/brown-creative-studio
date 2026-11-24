"""Publishing rules the studio adds to BrightBean's engine (apps/publisher/engine.py).

- **Assisted posting.** While TikTok Direct Post is off, a TikTok post is never
  sent to TikTok's API. When it is nearly due it becomes *Manual action needed*,
  a ``ManualPublishTask`` is opened for the assigned person, and the post is
  marked Published only when someone pastes the live link. A TikTok upload that
  lands in the creator's inbox takes the same route instead of reading Failed.
- **Publishing once.** Every move is a guarded, row-locked transition: routing
  twice opens one task, completing twice publishes once, and a manual post is
  out of the engine's reach (the engine only picks up ``scheduled`` rows).
- **Uncertain outcomes.** When Facebook or Instagram may have published before
  the error (a timeout, a dropped connection, a 5xx), the engine does not retry
  on its own. The post fails with a sentence asking someone to look, and a retry
  needs them to confirm it is not live — or they paste the link and it is
  marked Published.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.audit.services import record
from apps.publishing.models import ManualPublishTask

log = logging.getLogger(__name__)

# Platforms the studio publishes to (others are hidden: apps/studio/migrations/0003).
STUDIO_PLATFORMS = ("facebook", "instagram", "tiktok")
# Where an unknown outcome means "someone has to look before we try again".
CHECK_BEFORE_RETRY_PLATFORMS = ("facebook", "instagram")
OUTCOME_UNKNOWN_KEY = "outcome_unknown"
LIVE_URL_KEY = "live_url"

OUTCOME_UNKNOWN_MESSAGE = (
    "We lost contact with {platform} while publishing, so this post may or may not be live. "
    "Check the page before trying again: if it is there, paste its link to mark it published."
)

LIVE_URL_HOSTS = {
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com", "fb.watch"),
}


class PublishingError(ValueError):
    """A publishing action that can't be done; the message is for the person."""


# ── Assisted posting ─────────────────────────────────────────────────────────


def direct_post_enabled(platform: str) -> bool:
    if platform == "tiktok":
        return bool(getattr(settings, "TIKTOK_DIRECT_POST_ENABLED", False))
    return True


def manual_platforms() -> list[str]:
    """Platforms whose scheduled posts are posted by hand instead of through the API."""
    return [p for p in ("tiktok",) if not direct_post_enabled(p)]


def lead_time() -> timedelta:
    return timedelta(minutes=int(getattr(settings, "PUBLISHING_MANUAL_LEAD_MINUTES", 60)))


def due_at(platform_post):
    return platform_post.scheduled_at or platform_post.post.scheduled_at


def default_assignee(platform_post):
    """Who posts it: the post's author if they still work on this client, else the studio owner."""
    from apps.members.models import OrgMembership, WorkspaceMembership

    workspace = platform_post.post.workspace
    staff = WorkspaceMembership.objects.filter(workspace=workspace).exclude(
        workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT
    )
    author = platform_post.post.author
    if author and staff.filter(user=author).exists():
        return author
    owner = (
        OrgMembership.objects.filter(organization_id=workspace.organization_id, org_role=OrgMembership.OrgRole.OWNER)
        .select_related("user")
        .order_by("invited_at")
        .first()
    )
    return owner.user if owner else author


def route_due_manual_posts(now=None) -> int:
    """Move nearly-due hand-posted posts to Manual action needed. Called by the engine's cycle."""
    from apps.composer.models import PlatformPost

    platforms = manual_platforms()
    if not platforms:
        return 0
    now = now or timezone.now()
    ids = list(
        PlatformPost.objects.filter(status=PlatformPost.Status.SCHEDULED, social_account__platform__in=platforms)
        .annotate(effective_at=Coalesce("scheduled_at", "post__scheduled_at"))
        .filter(effective_at__lte=now + lead_time())
        .exclude(post__platform_posts__status=PlatformPost.Status.ON_HOLD)
        .values_list("id", flat=True)[:100]
    )
    routed = 0
    for pp_id in ids:
        try:
            if to_manual(pp_id, ManualPublishTask.Reason.DIRECT_POST_OFF, from_statuses=("scheduled",)):
                routed += 1
        except Exception:
            log.exception("Could not route PlatformPost %s to manual posting", pp_id)
    return routed


def to_manual(platform_post_id, reason, *, from_statuses, note: str = "") -> ManualPublishTask | None:
    """Move one post to Manual action needed and open its task, once.

    Returns the new task, or None when the post was no longer in ``from_statuses``
    (someone else moved it first) or its approval no longer covers it.
    """
    from apps.composer.models import PlatformPost
    from apps.reviews.services import schedule_block_reason

    with transaction.atomic():
        pp = (
            PlatformPost.objects.select_for_update(of=("self",))
            .select_related("post__workspace", "post__author", "social_account")
            .filter(pk=platform_post_id, status__in=from_statuses)
            .first()
        )
        if pp is None:
            return None
        if pp.status == PlatformPost.Status.SCHEDULED and schedule_block_reason(pp):
            # Changed after approval: the pre-publish check reports it; never hand it out.
            return None
        pp.status = PlatformPost.Status.MANUAL_ACTION
        pp.publish_error = note[:2000]
        pp.next_retry_at = None
        pp.save(update_fields=["status", "publish_error", "next_retry_at", "updated_at"])
        ManualPublishTask.objects.filter(platform_post=pp, status=ManualPublishTask.Status.OPEN).update(
            status=ManualPublishTask.Status.CANCELLED, cancelled_reason="Replaced"
        )
        task = ManualPublishTask.objects.create(
            workspace=pp.post.workspace,
            platform_post=pp,
            reason=reason,
            assignee=default_assignee(pp),
            due_at=due_at(pp) or timezone.now(),
        )
        record(
            "publishing.manual_needed",
            f"{pp.social_account.get_platform_display()} post needs posting by hand: {pp.post.title or 'Untitled'}.",
            workspace=pp.post.workspace,
            actor=None,
            target=pp.post,
            data={"platform_post": str(pp.id), "task": str(task.id), "reason": reason},
        )
    transaction.on_commit(lambda: notify_assignee(task))
    return task


def inbox_to_manual(platform_post, message: str) -> bool:
    """TikTok put the upload in the creator's inbox: someone finishes it in the app."""
    task = to_manual(
        platform_post.pk,
        ManualPublishTask.Reason.INBOX,
        from_statuses=("publishing",),
        note=message or "TikTok sent this video to the account's inbox. Open TikTok to finish posting it.",
    )
    return task is not None


def notify_assignee(task, *, reminder: bool = False) -> None:
    from django.urls import reverse

    from apps.notifications.engine import notify

    if task.assignee is None:
        return
    pp = task.platform_post
    platform = pp.social_account.get_platform_display()
    when = timezone.localtime(task.due_at, _zone(pp.post.workspace)).strftime("%a %-d %b, %H:%M")
    title = (
        f"Still to post on {platform}: {pp.post.workspace.name}"
        if reminder
        else f"Post on {platform} by hand: {pp.post.workspace.name}"
    )
    try:
        notify(
            task.assignee,
            "manual_publish",
            title=title,
            body=f"“{pp.post.title or 'Untitled'}” is due {when}. Download the video, copy the caption, "
            "post it, then paste the live link.",
            data={
                "task_id": str(task.id),
                "url": reverse("publishing:task", kwargs={"workspace_id": task.workspace_id, "task_id": task.id}),
            },
        )
    except Exception:
        log.exception("Could not notify %s about manual task %s", task.assignee_id, task.id)


def _zone(workspace):
    from apps.studio.decorators import zone

    return zone(workspace.effective_timezone)


def send_reminders(now=None) -> int:
    """Remind the assigned person once a hand-posted post is late (once per task)."""
    now = now or timezone.now()
    late = now - timedelta(minutes=int(getattr(settings, "PUBLISHING_MANUAL_REMINDER_MINUTES", 60)))
    sent = 0
    for task in ManualPublishTask.objects.filter(
        status=ManualPublishTask.Status.OPEN, due_at__lte=late, reminded_at__isnull=True
    ).select_related("platform_post__post__workspace", "platform_post__social_account", "assignee"):
        if close_if_stale(task):
            continue
        notify_assignee(task, reminder=True)
        ManualPublishTask.objects.filter(pk=task.pk).update(reminded_at=now)
        sent += 1
    return sent


def close_if_stale(task) -> bool:
    """An open task whose post left Manual action needed (reopened, sent back) is cancelled."""
    from apps.composer.models import PlatformPost

    status = PlatformPost.objects.filter(pk=task.platform_post_id).values_list("status", flat=True).first()
    if task.status == ManualPublishTask.Status.OPEN and status != PlatformPost.Status.MANUAL_ACTION:
        ManualPublishTask.objects.filter(pk=task.pk, status=ManualPublishTask.Status.OPEN).update(
            status=ManualPublishTask.Status.CANCELLED, cancelled_reason="The post was changed or sent back."
        )
        return True
    return False


def clean_live_url(platform: str, url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https://", url, re.IGNORECASE):
        raise PublishingError("Paste the full link to the live post, starting with https://.")
    host = re.sub(r"^https://", "", url, flags=re.IGNORECASE).split("/")[0].split(":")[0].lower()
    allowed = LIVE_URL_HOSTS.get(platform, ())
    if allowed and not any(host == h or host.endswith("." + h) for h in allowed):
        raise PublishingError(f"That isn't a {platform.title()} link. Paste the link to the live post.")
    return url[:1000]


def post_id_from_url(platform: str, url: str) -> str:
    patterns = {
        "tiktok": r"/video/(\d+)",
        "instagram": r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)",
        "facebook": r"(?:/posts/|/videos/|story_fbid=|fbid=)([A-Za-z0-9_.-]+)",
    }
    match = re.search(patterns.get(platform, r"$^"), url)
    return match.group(1) if match else ""


def complete_manual(task, url: str, actor) -> ManualPublishTask:
    """Someone posted it: store the live link and mark the post Published, exactly once."""
    from apps.composer.models import PlatformPost

    with transaction.atomic():
        task = ManualPublishTask.objects.select_for_update(of=("self",)).get(pk=task.pk)
        pp = (
            PlatformPost.objects.select_for_update(of=("self",))
            .select_related("post__workspace", "social_account")
            .get(pk=task.platform_post_id)
        )
        if task.status == ManualPublishTask.Status.DONE:
            raise PublishingError("This post is already marked as published.")
        if task.status != ManualPublishTask.Status.OPEN or pp.status != PlatformPost.Status.MANUAL_ACTION:
            raise PublishingError("This post is no longer waiting to be posted. Reload the page.")
        url = clean_live_url(pp.social_account.platform, url)
        live_id = post_id_from_url(pp.social_account.platform, url)
        now = timezone.now()
        task.status = ManualPublishTask.Status.DONE
        task.live_url, task.live_post_id = url, live_id
        task.completed_by, task.completed_at = actor, now
        task.save()
        _mark_live(pp, url, live_id, now)
        record(
            "publishing.manual_done",
            f"Marked published by hand on {pp.social_account.get_platform_display()}: {pp.post.title or 'Untitled'}.",
            workspace=pp.post.workspace,
            actor=actor,
            target=pp.post,
            data={"platform_post": str(pp.id), "task": str(task.id), "url": url},
        )
    _after_published(pp)
    return task


def _mark_live(pp, url: str, live_id: str, now) -> None:
    from apps.composer.models import PlatformPost
    from apps.publisher.models import PublishLog

    pp.status = PlatformPost.Status.PUBLISHED
    pp.published_at = now
    pp.platform_post_id = live_id or pp.platform_post_id
    pp.publish_error = ""
    extra = dict(pp.platform_extra or {})
    extra[LIVE_URL_KEY] = url
    extra.pop(OUTCOME_UNKNOWN_KEY, None)
    pp.platform_extra = extra
    pp.save(
        update_fields=["status", "published_at", "platform_post_id", "publish_error", "platform_extra", "updated_at"]
    )
    PublishLog.objects.create(platform_post=pp, attempt_number=pp.retry_count + 1, response_body=f"Live link: {url}")


def _after_published(pp) -> None:
    from apps.publisher.engine import PublishEngine

    try:
        engine = PublishEngine()
        engine._drop_queue_entry(pp)
        engine._sync_parent_published_at(pp.post)
    except Exception:
        log.exception("Post-publish bookkeeping failed for PlatformPost %s", pp.pk)


def reassign(task, user, actor) -> None:
    task.assignee = user
    task.save(update_fields=["assignee", "updated_at"])
    record(
        "publishing.reassigned",
        f"Posting by hand reassigned to {user.name or user.email}.",
        workspace=task.workspace,
        actor=actor,
        target=task.platform_post.post,
        data={"task": str(task.id)},
    )
    notify_assignee(task)


# ── Failures and safe retry ──────────────────────────────────────────────────


def outcome_unknown(platform_post, exc) -> bool:
    """Whether this failure may have happened after the platform published the post."""
    from apps.publisher.engine import _is_ambiguous_submission_failure

    if platform_post.social_account.platform not in CHECK_BEFORE_RETRY_PLATFORMS:
        return False
    if getattr(exc, "retryable", True) is False:
        return False
    if exc.__class__.__name__ in ("QuotaExceededError", "RateLimitError", "TokenExpiredError"):
        return False
    return _is_ambiguous_submission_failure(exc)


def flag_outcome_unknown(platform_post) -> None:
    from apps.composer.models import PlatformPost

    extra = dict(platform_post.platform_extra or {})
    extra[OUTCOME_UNKNOWN_KEY] = True
    PlatformPost.objects.filter(pk=platform_post.pk).update(platform_extra=extra)
    platform_post.platform_extra = extra


def unknown_message(platform_post) -> str:
    return OUTCOME_UNKNOWN_MESSAGE.format(platform=platform_post.social_account.get_platform_display())


def retry(platform_post, actor, *, confirmed_not_live: bool = False):
    """Put a failed post back in the queue to go out now, without risking a second copy."""
    from apps.composer.models import PlatformPost
    from apps.composer.services import require_publishable
    from apps.reviews.services import schedule_block_reason

    with transaction.atomic():
        pp = (
            PlatformPost.objects.select_for_update(of=("self",))
            .select_related("post__workspace", "social_account")
            .get(pk=platform_post.pk)
        )
        if pp.status != PlatformPost.Status.FAILED:
            raise PublishingError("Only a failed post can be retried.")
        if (pp.platform_extra or {}).get(OUTCOME_UNKNOWN_KEY) and not confirmed_not_live:
            raise PublishingError("Check the page first: confirm the post is not live before trying again.")
        if pp.social_account.needs_reconnect:
            raise PublishingError(
                f"Reconnect {pp.social_account.account_name} first: {pp.social_account.get_platform_display()} "
                "no longer accepts posts from this connection."
            )
        try:
            require_publishable(pp)
        except ValueError as exc:
            raise PublishingError(str(exc)) from exc
        reason = schedule_block_reason(pp)
        if reason:
            raise PublishingError(reason)
        pp.transition_to(PlatformPost.Status.SCHEDULED)
        pp.scheduled_at = timezone.now()
        extra = dict(pp.platform_extra or {})
        extra.pop(OUTCOME_UNKNOWN_KEY, None)
        pp.platform_extra = extra
        pp.save(update_fields=[*PlatformPost.TRANSITION_FIELDS, "scheduled_at", "platform_extra", "updated_at"])
        record(
            "publishing.retried",
            f"Publishing retried on {pp.social_account.get_platform_display()}: {pp.post.title or 'Untitled'}"
            + (" (checked: not live)." if confirmed_not_live else "."),
            workspace=pp.post.workspace,
            actor=actor,
            target=pp.post,
            data={"platform_post": str(pp.id), "confirmed_not_live": confirmed_not_live},
        )
    return pp


def mark_live(platform_post, url: str, actor):
    """A failed post that turned out to be live: store its link and mark it Published (no new attempt)."""
    from apps.composer.models import PlatformPost

    with transaction.atomic():
        pp = (
            PlatformPost.objects.select_for_update(of=("self",))
            .select_related("post__workspace", "social_account")
            .get(pk=platform_post.pk)
        )
        if pp.status != PlatformPost.Status.FAILED:
            raise PublishingError("Only a failed post can be marked as live.")
        url = clean_live_url(pp.social_account.platform, url)
        _mark_live(pp, url, post_id_from_url(pp.social_account.platform, url), timezone.now())
        record(
            "publishing.marked_live",
            f"Failed post found live on {pp.social_account.get_platform_display()}: {pp.post.title or 'Untitled'}.",
            workspace=pp.post.workspace,
            actor=actor,
            target=pp.post,
            data={"platform_post": str(pp.id), "url": url},
        )
    _after_published(pp)
    return pp


def alert_owners(platform_post, title: str, body: str, *, event_type: str = "post_failed", skip=()) -> None:
    """Tell the studio owners (who work on this client) about a publishing problem."""
    from apps.members.models import OrgMembership, WorkspaceMembership
    from apps.notifications.engine import notify

    workspace = platform_post.post.workspace
    owner_ids = set(
        OrgMembership.objects.filter(
            organization_id=workspace.organization_id, org_role=OrgMembership.OrgRole.OWNER
        ).values_list("user_id", flat=True)
    )
    members = (
        WorkspaceMembership.objects.filter(workspace=workspace, user_id__in=owner_ids)
        .exclude(workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT)
        .select_related("user")
    )
    skip_ids = {getattr(u, "pk", u) for u in skip if u}
    for m in members:
        if m.user_id in skip_ids:
            continue
        try:
            notify(
                m.user,
                event_type,
                title=title,
                body=body,
                data={"post_id": str(platform_post.post_id), "platform_post_id": str(platform_post.id)},
            )
        except Exception:
            log.exception("Could not alert owner %s about PlatformPost %s", m.user_id, platform_post.id)


def live_url(platform_post) -> str:
    return (platform_post.platform_extra or {}).get(LIVE_URL_KEY, "")
