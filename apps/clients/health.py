"""A client's health: overdue approvals, content runway and account connections.

Each signal is a word plus a tone, with a link to where it can be fixed.
Unpaid invoices and performance against goals join when billing (Phase 5) and
goals (Phase 7) exist; until then they are not shown rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

TONE_ORDER = {"success": 0, "neutral": 0, "warning": 1, "danger": 2}
REVIEW_STATUSES = ("pending_review", "pending_client")
PLANNED_STATUSES = (
    "draft",
    "pending_review",
    "pending_client",
    "approved",
    "changes_requested",
    "scheduled",
    "on_hold",
)
DEFAULT_POSTS_PER_MONTH = 12


@dataclass
class Signal:
    key: str
    label: str
    tone: str
    detail: str
    url: str = ""


@dataclass
class Health:
    signals: list[Signal] = field(default_factory=list)

    @property
    def tone(self) -> str:
        if not self.signals:
            return "neutral"
        return max(self.signals, key=lambda s: TONE_ORDER[s.tone]).tone

    @property
    def label(self) -> str:
        return {"danger": "Needs attention now", "warning": "Keep an eye on", "success": "On track"}.get(
            self.tone, "Not enough data"
        )


def post_due_at(platform_post):
    """When a post is due: its scheduled time, else the post's, else the proposed time."""
    post = platform_post.post
    return platform_post.scheduled_at or post.scheduled_at or post.proposed_publish_at


def approvals_signal(workspace, now) -> Signal:
    from apps.composer.models import PlatformPost

    waiting = list(
        PlatformPost.objects.filter(post__workspace=workspace, status__in=REVIEW_STATUSES).select_related("post")
    )
    url = reverse("calendar:calendar", kwargs={"workspace_id": workspace.id}) + "?mode=list&tab=approvals"
    if not waiting:
        return Signal("approvals", "Approvals", "success", "Nothing is waiting for approval.", url)
    posts = {pp.post_id: pp for pp in waiting}
    overdue = [pp for pp in posts.values() if (due := post_due_at(pp)) and due < now]
    soon = [pp for pp in posts.values() if (due := post_due_at(pp)) and now <= due < now + timedelta(hours=48)]
    if overdue:
        return Signal(
            "approvals", "Approvals", "danger", f"{len(overdue)} past their date and still waiting for approval.", url
        )
    if soon:
        return Signal("approvals", "Approvals", "warning", f"{len(soon)} due within 48 hours and still waiting.", url)
    return Signal("approvals", "Approvals", "success", f"{len(posts)} waiting, none due in the next 48 hours.", url)


def runway(workspace, now, days=30):
    """Planned posts in the next ``days`` and the last date covered."""
    from apps.composer.models import Post

    horizon = now + timedelta(days=days)
    posts = (
        Post.objects.filter(workspace=workspace, platform_posts__status__in=PLANNED_STATUSES)
        .filter(
            Q(scheduled_at__gte=now, scheduled_at__lt=horizon)
            | Q(scheduled_at__isnull=True, proposed_publish_at__gte=now, proposed_publish_at__lt=horizon)
        )
        .distinct()
    )
    dates = [p.scheduled_at or p.proposed_publish_at for p in posts]
    return len(dates), (max(dates) if dates else None)


def runway_signal(workspace, now, retainer=None) -> Signal:
    expected = retainer.posts_per_month if retainer and retainer.posts_per_month else DEFAULT_POSTS_PER_MONTH
    planned, last = runway(workspace, now)
    url = reverse("calendar:calendar", kwargs={"workspace_id": workspace.id})
    until = f" Nothing planned after {timezone.localtime(last):%b %-d}." if last else ""
    detail = f"{planned} of {expected} posts planned for the next 30 days.{until}"
    if planned < expected / 2:
        tone = "danger"
    elif planned < expected:
        tone = "warning"
    else:
        tone = "success"
    return Signal("runway", "Content runway", tone, detail, url)


def accounts_signal(workspace) -> Signal | None:
    from apps.social_accounts.models import SocialAccount

    accounts = list(SocialAccount.objects.for_workspace(workspace.id))
    if not accounts:
        return Signal(
            "accounts",
            "Accounts",
            "warning",
            "No social accounts connected yet.",
            reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id}),
        )
    broken = [a for a in accounts if a.connection_status != SocialAccount.ConnectionStatus.CONNECTED]
    url = reverse("social_accounts:list", kwargs={"workspace_id": workspace.id})
    if broken:
        names = ", ".join(f"{a.account_name} ({a.get_platform_display()})" for a in broken[:3])
        return Signal("accounts", "Accounts", "danger", f"Reconnect {names}.", url)
    return Signal("accounts", "Accounts", "success", f"{len(accounts)} connected.", url)


def client_health(workspace, now=None) -> Health:
    from apps.clients.models import Retainer

    now = now or timezone.now()
    retainer = Retainer.current_for(workspace)
    signals = [approvals_signal(workspace, now), runway_signal(workspace, now, retainer)]
    account = accounts_signal(workspace)
    if account:
        signals.append(account)
    return Health(signals=signals)
