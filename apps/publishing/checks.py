"""Pre-publish checks: is each upcoming post ready to go out, with enough notice to fix it?

For every post due in the next ``PUBLISHING_CHECK_HOURS`` (48 by default) this
checks the account connection, the approval, the media and who posts it by
hand. A problem within ``PUBLISHING_ALERT_HOURS`` (24) alerts the post's author
(or the person posting it) and the studio owners, once per problem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

log = logging.getLogger(__name__)

# Statuses that still have to go out at their time.
UPCOMING = ("draft", "pending_review", "pending_client", "changes_requested", "approved", "scheduled")


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "danger"  # danger: it will not go out as it stands; warning: it might not; info: a note


def check_hours() -> int:
    return int(getattr(settings, "PUBLISHING_CHECK_HOURS", 48))


def alert_hours() -> int:
    return int(getattr(settings, "PUBLISHING_ALERT_HOURS", 24))


def upcoming_posts(workspace_ids, now=None, hours=None):
    from apps.composer.models import PlatformPost
    from apps.publishing.services import STUDIO_PLATFORMS

    now = now or timezone.now()
    until = now + timedelta(hours=hours or check_hours())
    return list(
        PlatformPost.objects.filter(
            post__workspace_id__in=workspace_ids,
            status__in=UPCOMING,
            social_account__platform__in=STUDIO_PLATFORMS,
        )
        .annotate(due=Coalesce("scheduled_at", "post__scheduled_at", "post__proposed_publish_at"))
        .filter(due__gte=now - timedelta(minutes=5), due__lte=until)
        .select_related("post__workspace", "post__author", "social_account")
        .prefetch_related("post__media_attachments__media_asset")
        .order_by("due", "created_at")
    )


def check(pp, now=None) -> list[Issue]:
    """What stands between ``pp`` and going out at its time. ``pp.due`` must be annotated."""
    from apps.composer.services import publish_blocker, usable_media_types
    from apps.publishing.services import direct_post_enabled
    from apps.reviews.services import schedule_block_reason

    now = now or timezone.now()
    issues: list[Issue] = []
    account = pp.social_account
    platform = account.get_platform_display()

    # Connection
    if account.needs_reconnect:
        issues.append(Issue("disconnected", f"{account.account_name} on {platform} is disconnected. Reconnect it."))
    elif direct_post_enabled(account.platform) and account.token_expires_at and account.token_expires_at <= pp.due:
        issues.append(
            Issue(
                "token_expiring",
                f"The {platform} connection expires before this goes out. Reconnect {account.account_name}.",
                "warning",
            )
        )

    # Approval
    if pp.status == "scheduled":
        reason = schedule_block_reason(pp)
        if reason:
            issues.append(Issue("approval", reason))
    elif pp.status == "approved":
        issues.append(Issue("not_scheduled", "Approved but not scheduled yet.", "warning"))
    else:
        issues.append(Issue("not_approved", f"Not approved yet ({pp.get_status_display().lower()})."))

    # Media
    assets = [pm.media_asset for pm in pp.post.media_attachments.all()]
    blocker = publish_blocker(account, media_types=usable_media_types(assets), platform_extra=pp.platform_extra)
    if blocker:
        issues.append(Issue("media", blocker))
    processing = [a for a in assets if a.processing_status in ("pending", "processing")]
    failed = [a for a in assets if a.processing_status == "failed"]
    if failed:
        issues.append(Issue("media_failed", f"{len(failed)} attached file(s) failed processing. Replace them."))
    elif processing:
        issues.append(Issue("media_processing", "A file is still processing.", "warning"))

    # Posted by hand
    if not direct_post_enabled(account.platform):
        issues.append(
            Issue("manual", f"Posted by hand on {platform}: the assigned person is reminded before it is due.", "info")
        )
    return issues


def blocking(issues) -> list[Issue]:
    return [i for i in issues if i.severity != "info"]


def run_checks(now=None) -> int:
    """Alert about problems within the alert window, once per post, problem and time."""
    from apps.publishing.models import PublishAlert
    from apps.publishing.services import alert_owners, default_assignee
    from apps.workspaces.models import Workspace

    now = now or timezone.now()
    workspace_ids = list(Workspace.objects.filter(is_archived=False).values_list("id", flat=True))
    sent = 0
    for pp in upcoming_posts(workspace_ids, now, hours=alert_hours()):
        for issue in blocking(check(pp, now)):
            try:
                with transaction.atomic():
                    PublishAlert.objects.create(
                        workspace=pp.post.workspace,
                        platform_post=pp,
                        code=issue.code,
                        due_at=pp.due,
                        message=issue.message[:500],
                    )
            except IntegrityError:
                continue  # already told
            _alert(pp, issue, default_assignee(pp), alert_owners)
            sent += 1
    return sent


def _alert(pp, issue, person, alert_owners) -> None:
    from apps.notifications.engine import notify
    from apps.studio.decorators import zone

    when = timezone.localtime(pp.due, zone(pp.post.workspace.effective_timezone)).strftime("%a %-d %b, %H:%M")
    title = f"Fix before {when}: {pp.post.workspace.name} on {pp.social_account.get_platform_display()}"
    body = f"“{pp.post.title or 'Untitled'}”: {issue.message}"
    if person is not None:
        try:
            notify(person, "publish_check", title=title, body=body, data={"post_id": str(pp.post_id)})
        except Exception:
            log.exception("Pre-publish alert to %s failed", person.pk)
    alert_owners(pp, title, body, event_type="publish_check", skip=[person])
