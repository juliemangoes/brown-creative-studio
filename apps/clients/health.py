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
    """Planned posts against the package's posts per month. Without a package there is nothing to measure."""
    planned, last = runway(workspace, now)
    url = reverse("calendar:calendar", kwargs={"workspace_id": workspace.id})
    if not (retainer and retainer.posts_per_month):
        return Signal(
            "runway",
            "Content runway",
            "neutral",
            f"{planned} planned for the next 30 days. Set the package's posts per month (Services and retainer) "
            "to track the runway.",
            reverse("clients:services", kwargs={"workspace_id": workspace.id}),
        )
    expected = retainer.posts_per_month
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


def invoices_signal(workspace, today=None) -> Signal | None:
    """Unpaid invoices (apps.billing). Only for people who may see billing."""
    from apps.billing.models import Invoice

    open_invoices = list(
        Invoice.objects.filter(workspace=workspace, kind=Invoice.Kind.INVOICE, status__in=Invoice.OPEN_STATUSES)
    )
    if not open_invoices:
        return None
    url = reverse("billing:client", kwargs={"workspace_id": workspace.id})
    overdue = [i for i in open_invoices if i.status == Invoice.Status.OVERDUE]
    currency = open_invoices[0].currency
    if overdue:
        owed = sum((i.balance for i in overdue), 0)
        return Signal("invoices", "Invoices", "danger", f"{len(overdue)} overdue, {currency} {owed:,.2f} unpaid.", url)
    owed = sum((i.balance for i in open_invoices), 0)
    return Signal(
        "invoices", "Invoices", "neutral", f"{len(open_invoices)} open, {currency} {owed:,.2f} not yet due.", url
    )


def goals_signal(workspace, today=None) -> Signal | None:
    """Performance against the client's goals this period (apps.reports). None when no goals are set."""
    from apps.reports.figures import goals_progress

    progress = [g for g in goals_progress(workspace, today) if g.status != "no_data"]
    if not progress:
        return None
    url = reverse("reports:goals", kwargs={"workspace_id": workspace.id})
    behind = [g for g in progress if g.status == "behind"]
    if behind:
        names = ", ".join(g.goal.name for g in behind[:2])
        return Signal("goals", "Goals", "warning", f"{len(behind)} of {len(progress)} behind: {names}.", url)
    met = sum(1 for g in progress if g.status == "met")
    return Signal("goals", "Goals", "success", f"{len(progress)} on track" + (f", {met} met." if met else "."), url)


def client_health(workspace, now=None, *, include_billing=False) -> Health:
    from apps.clients.models import ClientProfile, Retainer

    now = now or timezone.now()
    stage = ClientProfile.objects.filter(workspace=workspace).values_list("stage", flat=True).first()
    if stage in (None, ClientProfile.Stage.ACTIVE):
        runway_check = runway_signal(workspace, now, Retainer.current_for(workspace))
    else:
        # Content runway only matters while the studio is producing for the client.
        planned, _ = runway(workspace, now)
        label = dict(ClientProfile.Stage.choices).get(stage or "", "")
        runway_check = Signal(
            "runway",
            "Content runway",
            "neutral",
            f"{planned} planned for the next 30 days. Tracked once the client is active (now: {label}).",
            reverse("calendar:calendar", kwargs={"workspace_id": workspace.id}),
        )
    signals = [approvals_signal(workspace, now), runway_check]
    account = accounts_signal(workspace)
    if account:
        signals.append(account)
    goals = goals_signal(workspace, timezone.localdate(now))
    if goals:
        signals.append(goals)
    if include_billing:
        invoices = invoices_signal(workspace)
        if invoices:
            signals.append(invoices)
    return Health(signals=signals)
