"""Planning: production stages, ideas into the calendar, the 13-week plan, warnings, key dates."""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record
from apps.planning.models import Campaign, ContentPlan, KeyDate

POST_STATUS_LABELS = {
    "draft": "Draft",
    "pending_review": "Pending review",
    "pending_client": "Pending client",
    "approved": "Approved",
    "changes_requested": "Changes requested",
    "rejected": "Rejected",
    "scheduled": "Scheduled",
    "publishing": "Publishing",
    "published": "Published",
    "failed": "Failed",
    "on_hold": "On hold",
    "manual_action": "Manual action needed",
}
DONE_STATUSES = {"scheduled", "publishing", "published", "manual_action"}
# Once a post is approved its production is finished: labels show the status alone.
STAGE_HIDDEN_STATUSES = DONE_STATUSES | {"approved"}
# Stages a post leaves automatically when it is sent for review.
BEFORE_REVIEW = ("idea", "planned", "shot_needed", "in_production")


# ── Plans and stages ─────────────────────────────────────────────────────────


def plan_for(post) -> ContentPlan:
    plan = ContentPlan.objects.filter(post=post).first()
    if plan is None:
        plan = ContentPlan.objects.create(post=post, workspace=post.workspace)
    return plan


def set_stage(plan: ContentPlan, stage: str, actor=None, *, note: str = "") -> bool:
    if stage == plan.stage or stage not in ContentPlan.Stage.values:
        return False
    previous = plan.get_stage_display()
    plan.stage = stage
    plan.save(update_fields=["stage", "updated_at"])
    record(
        "production.stage_changed",
        f"Production stage: {previous} → {plan.get_stage_display()}" + (f" ({note})" if note else ""),
        workspace=plan.workspace,
        actor=actor,
        target=plan.post,
        data={"from": previous, "to": plan.get_stage_display()},
    )
    return True


def combined_label(platform_post=None, *, post=None, plan=None, status=None) -> str:
    """One label for the calendar: production stage and editorial status, e.g. "Shot needed · Draft"."""
    if platform_post is not None:
        post = post or platform_post.post
        status = status or platform_post.status
    status_label = POST_STATUS_LABELS.get(status or "", (status or "").replace("_", " ").capitalize())
    if plan is None and post is not None:
        plan = ContentPlan.objects.filter(post_id=post.pk).only("stage").first()
    if plan is None or status in STAGE_HIDDEN_STATUSES:
        return status_label
    return f"{plan.get_stage_display()} · {status_label}"


def _target_datetime(workspace, day: date):
    import zoneinfo

    tz = zoneinfo.ZoneInfo(workspace.effective_timezone or "UTC")
    return datetime.combine(day, time(10, 0)).replace(tzinfo=tz)


def _connected_accounts(workspace):
    from apps.social_accounts.models import SocialAccount

    return list(
        SocialAccount.objects.for_workspace(workspace.id)
        .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .order_by("platform")
    )


@transaction.atomic
def quick_plan(
    workspace,
    actor,
    *,
    title,
    target_date,
    format="",
    pillar=None,
    campaign=None,
    stage=ContentPlan.Stage.PLANNED,
    caption="",
    accounts=None,
    media_asset_ids=(),
):
    """A planned post: a draft on the client's connected accounts, dated, with its production details."""
    from apps.composer.models import PlatformPost, Post, PostMedia

    post = Post.objects.create(
        workspace=workspace,
        author=actor,
        title=title[:255],
        caption=caption,
        category=pillar,
        proposed_publish_at=_target_datetime(workspace, target_date) if target_date else None,
    )
    for position, asset_id in enumerate(media_asset_ids):
        PostMedia.objects.create(post=post, media_asset_id=asset_id, position=position)
    for account in accounts if accounts is not None else _connected_accounts(workspace):
        PlatformPost.objects.create(post=post, social_account=account, status="draft")
    plan = ContentPlan.objects.create(
        post=post,
        workspace=workspace,
        stage=stage,
        format=format,
        campaign=campaign,
        target_date=target_date,
        owner=actor,
    )
    record(
        "production.planned",
        f"Planned for {target_date:%d %b %Y}" if target_date else "Planned",
        workspace=workspace,
        actor=actor,
        target=post,
    )
    return plan


@transaction.atomic
def plan_idea(idea, actor, *, target_date, format=""):
    """One action: an idea from the idea bank becomes a planned post on the calendar."""
    if idea.post_id:
        return plan_for(idea.post)
    media = [m.media_asset_id for m in idea.media_attachments.order_by("position") if m.media_asset_id]
    if not media and idea.media_asset_id:
        media = [idea.media_asset_id]
    plan = quick_plan(
        idea.workspace,
        actor,
        title=idea.title or "",
        caption=idea.description or "",
        target_date=target_date,
        format=format,
        stage=ContentPlan.Stage.PLANNED if media else ContentPlan.Stage.SHOT_NEEDED,
        media_asset_ids=media,
    )
    tags = [t.strip() for t in idea.tags if isinstance(t, str) and t.strip()] if isinstance(idea.tags, list) else []
    if tags:
        plan.post.tags = tags
        plan.post.save(update_fields=["tags", "updated_at"])
    idea.post = plan.post
    idea.save(update_fields=["post", "updated_at"])
    record(
        "production.from_idea",
        f"Planned from the idea “{idea.title}”",
        workspace=idea.workspace,
        actor=actor,
        target=plan.post,
    )
    return plan


# ── The plan, week by week ───────────────────────────────────────────────────


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass
class PlannedItem:
    post: object
    plan: object
    day: date | None
    status: str
    platforms: list
    has_media: bool

    @property
    def label(self) -> str:
        return combined_label(post=self.post, plan=self.plan, status=self.status)


@dataclass
class Week:
    start: date
    items: list = field(default_factory=list)
    key_dates: list = field(default_factory=list)
    campaigns: list = field(default_factory=list)
    expected: float = 0

    @property
    def end(self) -> date:
        return self.start + timedelta(days=6)

    @property
    def short(self) -> bool:
        return self.expected > 0 and len(self.items) < round(self.expected)


def _status_of(post) -> str:
    from apps.composer.status import derive_post_status

    return derive_post_status([pp.status for pp in post.platform_posts.all()]) if post.platform_posts.all() else "draft"


def _post_day(post, plan):
    when = post.scheduled_at or post.proposed_publish_at
    if when:
        return timezone.localtime(when).date()
    return plan.target_date if plan else None


def plan_weeks(workspaces, start: date, weeks: int = 13) -> list[Week]:
    """Posts, key dates and campaigns by week, for one client or several."""
    from apps.clients.models import Retainer
    from apps.composer.models import Post

    first = week_start(start)
    last = first + timedelta(days=7 * weeks)
    tz = timezone.get_current_timezone()
    lo, hi = (timezone.make_aware(datetime.combine(d, time.min), tz) for d in (first, last))
    ids = [ws.id for ws in workspaces]
    posts = (
        Post.objects.filter(workspace_id__in=ids)
        .filter(
            Q(scheduled_at__gte=lo, scheduled_at__lt=hi)
            | Q(scheduled_at__isnull=True, proposed_publish_at__gte=lo, proposed_publish_at__lt=hi)
            | Q(
                scheduled_at__isnull=True,
                proposed_publish_at__isnull=True,
                content_plan__target_date__gte=first,
                content_plan__target_date__lt=last,
            )
        )
        .select_related("workspace", "category", "content_plan", "content_plan__campaign")
        .prefetch_related("platform_posts__social_account", "media_attachments")
        .distinct()
    )
    result = OrderedDict((first + timedelta(days=7 * i), Week(first + timedelta(days=7 * i))) for i in range(weeks))
    for post in posts:
        plan = getattr(post, "content_plan", None)  # reverse one-to-one; None when the post has no plan
        day = _post_day(post, plan)
        if day is None or not (first <= day < last):
            continue
        status = _status_of(post)
        if status in ("rejected",):
            continue
        result[week_start(day)].items.append(
            PlannedItem(
                post,
                plan,
                day,
                status,
                [pp.social_account for pp in post.platform_posts.all()],
                bool(post.media_attachments.all()),
            )
        )
    org_ids = {ws.organization_id for ws in workspaces}
    for key in (
        KeyDate.objects.filter(organization_id__in=org_ids, date__gte=first, date__lt=last)
        .filter(Q(workspace__isnull=True) | Q(workspace_id__in=ids))
        .select_related("workspace")
    ):
        result[week_start(key.date)].key_dates.append(key)
    for campaign in Campaign.objects.filter(
        workspace_id__in=ids, starts_on__lt=last, ends_on__gte=first
    ).select_related("workspace"):
        for wk in result.values():
            if campaign.starts_on <= wk.end and campaign.ends_on >= wk.start:
                wk.campaigns.append(campaign)
    if len(workspaces) == 1:
        retainer = Retainer.current_for(workspaces[0])
        per_week = (retainer.posts_per_month * 12 / 52) if retainer and retainer.posts_per_month else 0
        for wk in result.values():
            wk.expected = per_week
    for wk in result.values():
        wk.items.sort(key=lambda i: (i.day, i.post.workspace.name))
    return list(result.values())


# ── Warnings ─────────────────────────────────────────────────────────────────


@dataclass
class Warning:
    tone: str  # warning | danger
    text: str
    url: str = ""


def planning_warnings(workspace, weeks: list[Week], today: date | None = None) -> list[Warning]:
    """Gaps, too much of one pillar or format, and posts close to their date with no asset or approval."""
    from django.urls import reverse

    today = today or timezone.localdate()
    found: list[Warning] = []
    for wk in weeks:
        if wk.short and wk.start >= week_start(today):
            found.append(
                Warning(
                    "warning",
                    f"Week of {wk.start:%-d %b}: {len(wk.items)} of about {round(wk.expected)} posts planned.",
                )
            )
    horizon = [i for wk in weeks for i in wk.items if today <= i.day <= today + timedelta(days=30)]
    if len(horizon) >= 4:
        for name, counter in (
            ("pillar", Counter(i.post.category.name for i in horizon if i.post.category_id)),
            ("format", Counter(i.plan.get_format_display() for i in horizon if i.plan and i.plan.format)),
        ):
            if counter:
                top, count = counter.most_common(1)[0]
                if count / len(horizon) > 0.5:
                    found.append(
                        Warning(
                            "warning", f"{count} of the next {len(horizon)} posts share one {name}: {top}. Mix it up."
                        )
                    )
    for item in horizon:
        url = reverse("planning:post", kwargs={"workspace_id": item.post.workspace_id, "post_id": item.post.id})
        days = (item.day - today).days
        title = item.post.title or item.post.caption[:40] or "Untitled post"
        if days <= 7 and not item.has_media and (not item.plan or item.plan.format != "text"):
            found.append(
                Warning(
                    "danger" if days <= 3 else "warning",
                    f"“{title}” goes out {item.day:%a %-d %b} with no photo or video yet.",
                    url,
                )
            )
        if days <= 3 and item.status in ("draft", "pending_review", "pending_client", "changes_requested"):
            found.append(
                Warning(
                    "danger",
                    f"“{title}” goes out {item.day:%a %-d %b} and isn't approved yet ({POST_STATUS_LABELS.get(item.status, item.status).lower()}).",
                    url,
                )
            )
    found.extend(rights_warnings(horizon))
    return found


def rights_warnings(items) -> list[Warning]:
    """Media whose usage rights expire before the post's date (``apps.assets.AssetRights``)."""
    from django.urls import reverse

    from apps.assets.models import AssetRights

    out = []
    asset_ids = {m.media_asset_id for i in items for m in i.post.media_attachments.all()}
    rights = {
        r.media_asset_id: r for r in AssetRights.objects.filter(media_asset_id__in=asset_ids, expires_on__isnull=False)
    }
    for item in items:
        for m in item.post.media_attachments.all():
            r = rights.get(m.media_asset_id)
            if r and r.expires_on < item.day:
                out.append(
                    Warning(
                        "danger",
                        f"The rights to {m.media_asset.filename} end {r.expires_on:%-d %b}, before “{item.post.title or 'this post'}” goes out.",
                        reverse(
                            "planning:post", kwargs={"workspace_id": item.post.workspace_id, "post_id": item.post.id}
                        ),
                    )
                )
    return out


# ── Key dates ────────────────────────────────────────────────────────────────


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lv = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lv) // 451
    month = (h + lv - 7 * m + 114) // 31
    day = ((h + lv - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def belize_holidays(year: int) -> list[tuple[date, str]]:
    """Belize's public holidays by their calendar dates. Some are observed on a nearby Monday; check the
    official list each year (Ministry of the Public Service) before relying on the exact day off."""
    easter = _easter(year)
    return [
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 3, 9), "National Heroes and Benefactors Day"),
        (easter - timedelta(days=2), "Good Friday"),
        (easter - timedelta(days=1), "Holy Saturday"),
        (easter + timedelta(days=1), "Easter Monday"),
        (date(year, 5, 1), "Labour Day"),
        (date(year, 8, 1), "Emancipation Day"),
        (date(year, 9, 10), "St. George's Caye Day"),
        (date(year, 9, 21), "Independence Day"),
        (date(year, 10, 12), "Indigenous Peoples' Resistance Day"),
        (date(year, 11, 19), "Garifuna Settlement Day"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Boxing Day"),
    ]


def ensure_holidays(organization, years) -> int:
    created = 0
    for year in years:
        for day, title in belize_holidays(year):
            _, made = KeyDate.objects.get_or_create(
                organization=organization,
                workspace=None,
                date=day,
                title=title,
                defaults={"kind": KeyDate.Kind.HOLIDAY},
            )
            created += made
    return created
