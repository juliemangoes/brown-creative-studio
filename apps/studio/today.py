"""What needs the studio's attention today, across every client the user works on.

Six lists, each item linking to the page where it gets resolved:
approvals waiting (soonest due first), posts going out in the next 7 days,
failed posts, content gaps in the next 30 days, onboarding that has stalled
and social accounts that need reconnecting.

Scope is always the user's own staff memberships in the current studio:
a workspace the user is not a member of, or is only a client of, never
contributes an item.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.clients.health import PLANNED_STATUSES, REVIEW_STATUSES, post_due_at, runway_signal
from apps.clients.models import ClientProfile, Retainer, ServiceLine
from apps.clients.services import profile_for
from apps.members.models import WorkspaceMembership

UPCOMING_STATUSES = (*PLANNED_STATUSES, "publishing")
FAILED_LOOKBACK_DAYS = 30
# Stages whose content the studio is producing now.
PRODUCING_STAGES = (ClientProfile.Stage.ACTIVE,)
# Stages whose social accounts the studio is responsible for.
ACCOUNT_STAGES = (ClientProfile.Stage.ONBOARDING, ClientProfile.Stage.ACTIVE, ClientProfile.Stage.PAUSED)


def stall_days() -> int:
    return int(getattr(settings, "STUDIO_ONBOARDING_STALL_DAYS", 14))


def staff_memberships(user, organization):
    """The user's non-archived workspace memberships in ``organization`` with a studio (non-client) role."""
    return list(
        WorkspaceMembership.objects.filter(
            user=user, workspace__organization=organization, workspace__is_archived=False
        )
        .exclude(workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT)
        .select_related("workspace")
        .select_related("custom_role")
        .order_by("workspace__name")
    )


@dataclass
class PostItem:
    workspace: Any
    post: Any
    due: datetime | None
    statuses: list[str]
    platforms: list[str]
    error: str = ""

    @property
    def status(self) -> str:
        """The status that matters most for this post across its platforms."""
        for status in (
            "failed",
            "pending_client",
            "pending_review",
            "changes_requested",
            "draft",
            "approved",
            "scheduled",
        ):
            if status in self.statuses:
                return status
        return self.statuses[0] if self.statuses else "draft"

    @property
    def edit_url(self) -> str:
        return reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id})


@dataclass
class ApprovalItem(PostItem):
    overdue: bool = False
    due_soon: bool = False

    @property
    def waiting_on(self) -> str:
        return "Client" if "pending_client" in self.statuses else "Internal review"

    @property
    def review_url(self) -> str:
        return reverse("calendar:calendar", kwargs={"workspace_id": self.workspace.id}) + "?mode=list&tab=approvals"


@dataclass
class Today:
    workspaces: list
    selected: Any
    approvals: list[ApprovalItem] = field(default_factory=list)
    upcoming: OrderedDict = field(default_factory=OrderedDict)  # date -> [PostItem]
    upcoming_count: int = 0
    failed: list[PostItem] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    stalled: list[dict] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)
    overdue_tasks: list = field(default_factory=list)
    brand_signoffs: list = field(default_factory=list)
    shoots: list[dict] = field(default_factory=list)  # upcoming shoot days and what is missing
    needs_shoot: list = field(default_factory=list)  # planned posts needing a shot, on no shoot yet
    overdue_invoices: list = field(default_factory=list)  # only where the person sees billing
    payments_to_confirm: int = 0
    manual_posts: list = field(default_factory=list)  # ManualPublishTask waiting to be posted by hand
    publish_problems: list = field(default_factory=list)  # (platform_post, issues) due within a day

    @property
    def attention_count(self) -> int:
        overdue = sum(1 for a in self.approvals if a.overdue)
        return (
            overdue
            + len(self.failed)
            + len(self.accounts)
            + len(self.stalled)
            + len(self.overdue_tasks)
            + len(self.overdue_invoices)
            + self.payments_to_confirm
            + len(self.manual_posts)
            + len(self.publish_problems)
            + sum(1 for b in self.brand_signoffs if b.status == "changes_requested")
            + sum(1 for g in self.gaps if g["signal"].tone == "danger")
        )


def _group_by_post(platform_posts):
    grouped: OrderedDict = OrderedDict()
    for pp in platform_posts:
        entry = grouped.setdefault(pp.post_id, {"pp": pp, "statuses": [], "platforms": [], "errors": []})
        if pp.status not in entry["statuses"]:
            entry["statuses"].append(pp.status)
        platform = pp.social_account.get_platform_display() if pp.social_account_id else ""
        if platform and platform not in entry["platforms"]:
            entry["platforms"].append(platform)
        if pp.publish_error:
            entry["errors"].append(pp.publish_error)
    return grouped.values()


def posts_by_post(platform_posts) -> list[PostItem]:
    """One item per post (platforms and statuses combined), in the queryset's order."""
    return [
        PostItem(
            workspace=entry["pp"].post.workspace,
            post=entry["pp"].post,
            due=post_due_at(entry["pp"]),
            statuses=entry["statuses"],
            platforms=entry["platforms"],
            error=(entry["errors"][0] if entry["errors"] else ""),
        )
        for entry in _group_by_post(platform_posts)
    ]


def _platform_posts(workspace_ids):
    from apps.composer.models import PlatformPost

    return PlatformPost.objects.filter(post__workspace_id__in=workspace_ids).select_related(
        "post__workspace", "social_account"
    )


def approvals(workspace_ids, now) -> list[ApprovalItem]:
    rows = []
    for entry in _group_by_post(_platform_posts(workspace_ids).filter(status__in=REVIEW_STATUSES)):
        pp = entry["pp"]
        due = post_due_at(pp)
        rows.append(
            ApprovalItem(
                workspace=pp.post.workspace,
                post=pp.post,
                due=due,
                statuses=entry["statuses"],
                platforms=entry["platforms"],
                overdue=bool(due and due < now),
                due_soon=bool(due and now <= due < now + timedelta(hours=48)),
            )
        )
    far_future = now + timedelta(days=36500)
    rows.sort(key=lambda r: (r.due is None, r.due or far_future, r.workspace.name))
    return rows


def upcoming(workspace_ids, now, days=7):
    qs = (
        _platform_posts(workspace_ids)
        .filter(status__in=UPCOMING_STATUSES, scheduled_at__gte=now, scheduled_at__lt=now + timedelta(days=days))
        .order_by("scheduled_at", "created_at")  # platforms in the order they were added
    )
    by_day: OrderedDict = OrderedDict()
    count = 0
    for entry in _group_by_post(qs):
        pp = entry["pp"]
        day = timezone.localtime(pp.scheduled_at).date()
        by_day.setdefault(day, []).append(
            PostItem(
                workspace=pp.post.workspace,
                post=pp.post,
                due=pp.scheduled_at,
                statuses=entry["statuses"],
                platforms=entry["platforms"],
            )
        )
        count += 1
    return by_day, count


def failed(workspace_ids, now) -> list[PostItem]:
    qs = (
        _platform_posts(workspace_ids)
        .filter(status="failed", updated_at__gte=now - timedelta(days=FAILED_LOOKBACK_DAYS))
        .order_by("-updated_at")
    )
    return posts_by_post(qs)


def manual_posts(memberships, workspace_ids) -> list:
    """Posts waiting to be posted by hand (apps.publishing), where the person sees the plan."""
    from apps.publishing.models import ManualPublishTask

    ids = [m.workspace_id for m in memberships if m.workspace_id in workspace_ids and _sees_plan(m)]
    return list(
        ManualPublishTask.objects.filter(
            workspace_id__in=ids,
            status=ManualPublishTask.Status.OPEN,
            platform_post__status="manual_action",
        )
        .select_related("workspace", "assignee", "platform_post__post", "platform_post__social_account")
        .order_by("due_at")
    )


def publish_problems(memberships, workspace_ids, now) -> list:
    """Upcoming posts (next day) that won't go out as they stand: connection, approval or media."""
    from apps.publishing import checks

    ids = [m.workspace_id for m in memberships if m.workspace_id in workspace_ids and _sees_plan(m)]
    rows = []
    for pp in checks.upcoming_posts(ids, now, hours=checks.alert_hours()):
        problems = [i for i in checks.check(pp, now) if i.severity == "danger"]
        if problems:
            rows.append((pp, problems))
    return rows


def _sees_plan(membership) -> bool:
    return bool(membership.effective_permissions.get("view_plan", False))


def content_gaps(workspaces, profiles, now) -> list[dict]:
    rows = []
    for ws in workspaces:
        profile = profiles[ws.id]
        if profile.stage not in PRODUCING_STAGES:
            continue
        social = ServiceLine.objects.for_workspace(ws.id).filter(kind=ServiceLine.Kind.SOCIAL_MEDIA).first()
        if social and social.status != ServiceLine.Status.ACTIVE:
            continue  # social media is paused or ended for this client
        signal = runway_signal(ws, now, Retainer.current_for(ws))
        if signal.tone in ("warning", "danger"):  # no package means nothing to measure against
            rows.append({"workspace": ws, "signal": signal})
    rows.sort(key=lambda r: (r["signal"].tone != "danger", r["workspace"].name))
    return rows


def idle_days() -> int:
    return int(getattr(settings, "STUDIO_ONBOARDING_IDLE_DAYS", 5))


def stalled_onboarding(workspaces, profiles, now, can_view_profile) -> list[dict]:
    """Onboarding nobody on the client side has touched for a while, or that has run too long.

    Also a client sitting in the Onboarding stage with no onboarding started.
    """
    from apps.client_onboarding.models import OnboardingRun

    too_long = now - timedelta(days=stall_days())
    idle = now - timedelta(days=idle_days())
    runs = {
        r.workspace_id: r
        for r in OnboardingRun.objects.filter(
            workspace__in=workspaces, status=OnboardingRun.Status.IN_PROGRESS
        ).prefetch_related("steps")
    }
    rows = []
    for ws in workspaces:
        if not can_view_profile.get(ws.id):
            continue  # the link leads to the client record
        run = runs.get(ws.id)
        if run is not None:
            last = run.last_client_activity_at or run.started_at
            if last < idle or run.started_at < too_long:
                done, total = run.progress()
                waiting_client = any(not s.is_finished and not s.is_studio_step for s in run.steps.all())
                rows.append(
                    {
                        "workspace": ws,
                        "days": (now - run.started_at).days,
                        "quiet_days": (now - last).days,
                        "done": done,
                        "total": total,
                        "waiting_on": "client" if waiting_client else "studio",
                        "has_run": True,
                    }
                )
            continue
        profile = profiles[ws.id]
        if profile.stage == ClientProfile.Stage.ONBOARDING and profile.stage_changed_at < too_long:
            rows.append({"workspace": ws, "days": (now - profile.stage_changed_at).days, "has_run": False})
    rows.sort(key=lambda r: -r["days"])
    return rows


def overdue_tasks(user, organization, workspace_ids, today):
    """Open tasks past their due date in the clients this person sees (freelancers: their own)."""
    from apps.projects.models import Task
    from apps.projects.services import studio_scope

    scope = studio_scope(user, organization)
    return list(
        scope.tasks(user)
        .filter(workspace_id__in=workspace_ids, due_on__lt=today)
        .exclude(status=Task.Status.DONE)
        .select_related("project", "workspace", "assignee")
        .order_by("due_on")[:20]
    )


def brand_signoffs(workspace_ids, can_view_profile):
    """Brand stages waiting for the client's sign-off, and ones the client sent back."""
    from apps.brand.models import BrandStage

    ids = [i for i in workspace_ids if can_view_profile.get(i)]
    return list(
        BrandStage.objects.filter(
            workspace_id__in=ids, status__in=[BrandStage.Status.AWAITING_SIGNOFF, BrandStage.Status.CHANGES_REQUESTED]
        )
        .select_related("workspace")
        .order_by("-status", "sent_at")
    )


def upcoming_shoots(user, memberships, workspace_ids, today, days=14) -> list[dict]:
    """Shoot days in the next two weeks this person can see, with what is still missing.

    Contributors without ``manage_shoots`` see only the shoots they are crew on.
    """
    from apps.shoots import services as shoots
    from apps.shoots.models import Shoot

    rows = []
    for m in memberships:
        if m.workspace_id not in workspace_ids or not m.effective_permissions.get("view_shoots", False):
            continue
        qs = Shoot.objects.filter(
            workspace_id=m.workspace_id, date__gte=today, date__lte=today + timedelta(days=days)
        ).exclude(status__in=[Shoot.Status.CANCELLED, Shoot.Status.DONE])
        if shoots.crew_only(m):
            qs = qs.filter(crew=user)
        for shoot in qs.select_related("workspace").prefetch_related("shots", "crew"):
            rows.append(
                {
                    "shoot": shoot,
                    "missing": shoots.readiness(shoot),
                    "shots": len(shoot.shots.all()),
                    "url": reverse("shoots:detail", kwargs={"workspace_id": shoot.workspace_id, "shoot_id": shoot.pk}),
                }
            )
    return sorted(rows, key=lambda r: (r["shoot"].date, r["shoot"].call_time))


def needs_shoot(memberships, workspace_ids, today):
    """Posts marked "shot needed" soon that no upcoming shoot covers (only where the person can plan shoots)."""
    from apps.shoots.services import posts_needing_shots

    ids = [
        m.workspace_id
        for m in memberships
        if m.workspace_id in workspace_ids and m.effective_permissions.get("manage_shoots", False)
    ]
    return posts_needing_shots(ids, today) if ids else []


def billing_items(memberships, workspace_ids):
    """Overdue invoices and client payments waiting for confirmation, where the person sees billing."""
    from apps.billing.models import Invoice, Payment

    ids = [
        m.workspace_id
        for m in memberships
        if m.workspace_id in workspace_ids and m.effective_permissions.get("view_billing")
    ]
    if not ids:
        return [], 0
    overdue = list(
        Invoice.objects.filter(workspace_id__in=ids, status=Invoice.Status.OVERDUE)
        .select_related("workspace")
        .order_by("due_date")[:20]
    )
    pending = Payment.objects.filter(workspace_id__in=ids, status=Payment.Status.PENDING).count()
    return overdue, pending


def account_problems(workspaces, profiles) -> list[dict]:
    from apps.social_accounts.models import SocialAccount

    in_scope = [ws.id for ws in workspaces if profiles[ws.id].stage in ACCOUNT_STAGES]
    accounts = (
        SocialAccount.objects.filter(workspace_id__in=in_scope)
        .exclude(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .select_related("workspace")
        .order_by("workspace__name", "platform")
    )
    return [
        {
            "workspace": account.workspace,
            "account": account,
            "tone": "warning"
            if account.connection_status == SocialAccount.ConnectionStatus.TOKEN_EXPIRING
            else "danger",
            "url": reverse("social_accounts:list", kwargs={"workspace_id": account.workspace_id}),
        }
        for account in accounts
    ]


def build_today(user, organization, selected=None, now=None) -> Today:
    """Everything on Today for ``user`` in ``organization``, optionally for one client.

    ``selected`` must be one of the user's staff workspaces; anything else
    yields an empty page rather than another client's data.
    """
    now = now or timezone.now()
    memberships = staff_memberships(user, organization)
    workspaces = [m.workspace for m in memberships]
    can_view_profile = {m.workspace_id: m.effective_permissions.get("view_client_profile", False) for m in memberships}
    scoped = [ws for ws in workspaces if selected is None or ws.id == selected.id]
    ids = [ws.id for ws in scoped]
    profiles = {ws.id: profile_for(ws) for ws in scoped}
    by_day, count = upcoming(ids, now)
    overdue, pending = billing_items(memberships, ids)
    return Today(
        workspaces=workspaces,
        selected=selected,
        approvals=approvals(ids, now),
        upcoming=by_day,
        upcoming_count=count,
        failed=failed(ids, now),
        gaps=content_gaps(scoped, profiles, now),
        stalled=stalled_onboarding(scoped, profiles, now, can_view_profile),
        accounts=account_problems(scoped, profiles),
        overdue_tasks=overdue_tasks(user, organization, ids, timezone.localdate(now)),
        brand_signoffs=brand_signoffs(ids, can_view_profile),
        shoots=upcoming_shoots(user, memberships, ids, timezone.localdate(now)),
        needs_shoot=needs_shoot(memberships, ids, timezone.localdate(now)),
        overdue_invoices=overdue,
        payments_to_confirm=pending,
        manual_posts=manual_posts(memberships, ids),
        publish_problems=publish_problems(memberships, ids, now),
    )
