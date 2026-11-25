"""Figures for a client and a period: totals, comparisons, published posts, breakdowns and goals.

Reads BrightBean's daily snapshots (``AccountInsightsSnapshot``,
``PostInsightsSnapshot``) and figures imported from CSV. Every ``Figure`` says
where it came from, when it was last updated, and whether we calculated it.
A metric a platform does not give is ``available=False`` with the reason; it
is never shown as zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.reports import catalog
from apps.reports.catalog import PLATFORMS

CALCULATED_NOTE = "Calculated by the studio from the platform's running totals."
COMBINED_NOTE = "Total across platforms, which each count this their own way."
NEVER_COMBINED_NOTE = "Not added up across platforms: someone who saw two accounts would count twice."
SMALL_SAMPLE = 3
# A comparison needs data on most days of the previous period; otherwise it reads as growth.
MIN_COVERAGE = 0.8
PARTIAL_PREVIOUS_NOTE = "No comparison: there is too little data for the previous period."


@dataclass
class Figure:
    metric: str
    platform: str
    value: float | None = None
    previous: float | None = None
    available: bool = True
    missing: str = ""
    calculated: bool = False
    imported: bool = False
    combined: bool = False
    updated_at: str = ""  # ISO timestamp of the newest data used
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    series: list[list[Any]] = field(default_factory=list)  # [[iso date, value or None], ...]

    @property
    def change_pct(self) -> float | None:
        if self.value is None or not self.previous:
            return None
        return round((self.value - self.previous) / self.previous * 100, 1)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["change_pct"] = self.change_pct
        return data


@dataclass
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        return Period(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(self.days)]


def month_period(year: int, month: int) -> Period:
    start = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return Period(start, nxt - timedelta(days=1))


def last_days(days: int, today: date | None = None) -> Period:
    today = today or timezone.localdate()
    return Period(today - timedelta(days=days - 1), today)


# ── Accounts ─────────────────────────────────────────────────────────────────


def accounts(workspace, platform: str = "all") -> list:
    """The client's accounts on the studio's platforms, connected or not (their history still counts)."""
    from apps.social_accounts.models import SocialAccount

    qs = SocialAccount.objects.filter(workspace=workspace, platform__in=PLATFORMS).order_by("platform", "account_name")
    if platform != "all":
        qs = qs.filter(platform=platform)
    return list(qs)


# ── Daily series per account ─────────────────────────────────────────────────


@dataclass
class _Series:
    by_day: dict[date, float]
    updated_at: datetime | None = None
    calculated: bool = False
    imported: bool = False


def _account_rows(account, keys, start, end):
    from apps.analytics.models import AccountInsightsSnapshot

    return AccountInsightsSnapshot.objects.filter(
        social_account=account, metric_key__in=keys, date__gte=start, date__lte=end
    ).values_list("metric_key", "date", "value", "captured_at")


def _key_series(account, key: str, start: date, end: date, *, posts_only: bool) -> _Series:
    """One snapshot key, daily. Falls back to per-post deltas (calculated) when there is no account figure."""
    from apps.analytics.services import _post_summed_series_for_metric, _supports_post_fallback

    out = _Series({})
    if not posts_only:
        for _key, day, value, captured in _account_rows(account, [key], start, end):
            out.by_day[day] = out.by_day.get(day, 0.0) + float(value)
            out.updated_at = max(filter(None, [out.updated_at, captured]), default=None)
    if not out.by_day and _supports_post_fallback(key):
        daily, captured, observed = _post_summed_series_for_metric(account, key, start, end)
        if observed:
            out = _Series(dict(daily), captured, calculated=True)
    return out


def _with_imports(account, metric: str, series: _Series, start: date, end: date) -> _Series:
    from apps.reports.models import ImportedMetric

    for day, value, imported_at in ImportedMetric.objects.filter(
        social_account=account, metric=metric, date__gte=start, date__lte=end
    ).values_list("date", "value", "imported_at"):
        if day not in series.by_day:
            series.by_day[day] = float(value)
            series.imported = True
            series.updated_at = max(filter(None, [series.updated_at, imported_at]), default=None)
    return series


def account_series(account, metric: str, start: date, end: date) -> _Series | None:
    """Daily values of a studio metric for one account, or None when the platform doesn't give it."""
    src = catalog.source(metric, account.platform)
    if src.missing or not src.keys:
        return None
    if src.how == "total":
        return _growth_series(account, src.keys[0], start, end)
    total = _Series({})
    for key in src.keys:
        part = _key_series(account, key, start, end, posts_only=src.how == "posts")
        for day, value in part.by_day.items():
            total.by_day[day] = total.by_day.get(day, 0.0) + value
        total.calculated |= part.calculated
        total.updated_at = max(filter(None, [total.updated_at, part.updated_at]), default=None)
    return _with_imports(account, metric, total, start, end)


def _growth_series(account, key: str, start: date, end: date) -> _Series:
    """Daily change in a running total (Instagram followers). Calculated, never a platform figure."""
    from apps.analytics.models import AccountInsightsSnapshot

    rows = list(
        AccountInsightsSnapshot.objects.filter(
            social_account=account, metric_key=key, date__gte=start - timedelta(days=7), date__lte=end
        )
        .order_by("date")
        .values_list("date", "value", "captured_at")
    )
    out = _Series({}, calculated=True)
    previous = None
    for day, value, captured in rows:
        if previous is not None and start <= day <= end:
            out.by_day[day] = float(value) - previous
        previous = float(value)
        out.updated_at = max(filter(None, [out.updated_at, captured]), default=None)
    return out


# ── Figures ──────────────────────────────────────────────────────────────────


def _platform_label(platform: str) -> str:
    return str(catalog.PLATFORM_LABELS.get(platform, platform))


def _break_note(platform: str, metric: str, period: Period) -> tuple[str, bool]:
    """(note, comparison_broken) when a platform changed this metric inside or between the periods."""
    entry = catalog.BREAKS.get((platform, metric))
    if not entry:
        return "", False
    when, note = entry
    prev = period.previous()
    if prev.start < when <= period.end:
        return str(note), when > prev.end or when > prev.start
    return "", False


def _coverage(series_list: list[_Series], period: Period) -> float:
    days = {d for s in series_list for d in s.by_day if period.start <= d <= period.end}
    return len(days) / period.days


def _period_total(series_list: list[_Series], period: Period, kind: str) -> float | None:
    days = [d for s in series_list for d in s.by_day if period.start <= d <= period.end]
    if not days:
        return None
    total = sum(v for s in series_list for d, v in s.by_day.items() if period.start <= d <= period.end)
    return float(total)


def platform_figure(workspace, metric: str, platform: str, period: Period) -> Figure:
    m = catalog.metric(metric)
    src = catalog.source(metric, platform)
    fig = Figure(metric=metric, platform=platform)
    accts = accounts(workspace, platform)
    if src.missing:
        fig.available, fig.missing = False, str(src.missing)
        return fig
    if not accts:
        fig.available, fig.missing = False, "No account connected"
        return fig
    if metric == "engagement_rate":
        return _rate(workspace, platform, period, accts)
    prev = period.previous()
    cur_series, prev_series = [], []
    for account in accts:
        cur = account_series(account, metric, period.start, period.end)
        old = account_series(account, metric, prev.start, prev.end)
        if cur is not None:
            cur_series.append(cur)
        if old is not None:
            prev_series.append(old)
    fig.value = _period_total(cur_series, period, m.kind)
    fig.previous = _period_total(prev_series, prev, m.kind)
    if fig.previous is not None and _coverage(prev_series, prev) < MIN_COVERAGE:
        fig.previous = None
        fig.notes.append(PARTIAL_PREVIOUS_NOTE)
    fig.calculated = any(s.calculated for s in cur_series)
    fig.imported = any(s.imported for s in cur_series)
    newest = max((s.updated_at for s in cur_series if s.updated_at), default=None)
    fig.updated_at = newest.isoformat() if newest else ""
    fig.sources = [f"{_platform_label(platform)}: {src.field}"]
    if fig.calculated:
        fig.notes.append(CALCULATED_NOTE)
    if fig.imported:
        fig.notes.append("Includes figures imported from a file.")
    note, broken = _break_note(platform, metric, period)
    if note:
        fig.notes.append(note)
        if broken:
            fig.previous = None
    if fig.value is None:
        fig.available, fig.missing = False, "No data for this period yet"
    fig.series = [
        [
            d.isoformat(),
            sum(s.by_day[d] for s in cur_series if d in s.by_day) if any(d in s.by_day for s in cur_series) else None,
        ]
        for d in period.dates()
    ]
    return fig


def _rate(workspace, platform: str, period: Period, accts) -> Figure:
    eng = platform_figure(workspace, "engagement", platform, period)
    views = platform_figure(workspace, "views", platform, period)
    fig = Figure(metric="engagement_rate", platform=platform)
    fig.sources = [f"{_platform_label(platform)}: engagement / views"]
    fig.calculated = True
    fig.updated_at = max(eng.updated_at, views.updated_at)
    if not (eng.available and views.available and views.value):
        fig.available, fig.missing = False, (eng.missing or views.missing or "No views in this period")
        return fig
    fig.value = round((eng.value or 0) / views.value * 100, 2)
    if eng.previous is not None and views.previous:
        fig.previous = round(eng.previous / views.previous * 100, 2)
    fig.notes = sorted(set(eng.notes + views.notes))
    return fig


def figure(workspace, metric: str, platform: str, period: Period) -> Figure:
    """A metric for one platform, or for all of the client's platforms (labelled when combined)."""
    if platform != "all":
        return platform_figure(workspace, metric, platform, period)
    m = catalog.metric(metric)
    present = sorted({a.platform for a in accounts(workspace)})
    fig = Figure(metric=metric, platform="all", combined=len(present) > 1)
    if not present:
        fig.available, fig.missing = False, "No account connected"
        return fig
    parts = [platform_figure(workspace, metric, p, period) for p in present]
    usable = [p for p in parts if p.available and p.value is not None]
    fig.combined = len(usable) > 1
    if not usable:
        fig.available, fig.combined = False, False
        reasons = {p.missing for p in parts}
        fig.missing = reasons.pop() if len(reasons) == 1 else "Not available for these accounts"
        if len(parts) > 1:
            fig.notes = [f"{_platform_label(p.platform)}: {p.missing}" for p in parts]
        return fig
    if m.combine == "never" and len(usable) == 1:
        # Only one platform gives this metric: show it as that platform's figure, never as a total.
        only = usable[0]
        fig.combined = False
        fig.value, fig.previous, fig.calculated, fig.imported = (
            only.value,
            only.previous,
            only.calculated,
            only.imported,
        )
        fig.updated_at, fig.sources, fig.series = only.updated_at, only.sources, only.series
        fig.notes = ([f"{_platform_label(only.platform)} only."] if len(present) > 1 else []) + list(only.notes)
        return fig
    if m.combine == "never":
        fig.available, fig.missing, fig.combined = False, NEVER_COMBINED_NOTE, False
        fig.notes = [f"{_platform_label(p.platform)}: {_fmt(p)}" for p in parts]
        return fig
    if metric == "engagement_rate":
        eng = figure(workspace, "engagement", "all", period)
        views = figure(workspace, "views", "all", period)
        if eng.value is not None and views.value:
            fig.value = round(eng.value / views.value * 100, 2)
        if eng.previous is not None and views.previous:
            fig.previous = round(eng.previous / views.previous * 100, 2)
        fig.calculated = True
    else:
        fig.value = sum(p.value or 0 for p in usable)
        prevs = [p.previous for p in usable if p.previous is not None]
        fig.previous = sum(prevs) if len(prevs) == len(usable) else None
        fig.calculated = any(p.calculated for p in usable)
    fig.imported = any(p.imported for p in usable)
    fig.updated_at = max((p.updated_at for p in usable), default="")
    fig.sources = [s for p in usable for s in p.sources]
    skipped = [p for p in parts if not (p.available and p.value is not None)]
    if fig.combined:
        fig.notes.append(COMBINED_NOTE)
    elif len(usable) == 1 and len(present) > 1:
        fig.notes.append(f"{_platform_label(usable[0].platform)} only.")
    for p in skipped:
        fig.notes.append(f"{_platform_label(p.platform)}: {p.missing}")
    for p in usable:
        fig.notes.extend(n for n in p.notes if n not in fig.notes)
    if usable and usable[0].series:
        dates = [row[0] for row in usable[0].series]
        fig.series = [
            [d, _sum_or_none(p.series[i][1] for p in usable if len(p.series) > i)] for i, d in enumerate(dates)
        ]
    return fig


def _sum_or_none(values) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def _fmt(fig: Figure) -> str:
    if not fig.available or fig.value is None:
        return fig.missing
    return f"{fig.value:,.0f}"


def overview(workspace, platform: str, period: Period, metrics=None) -> list[Figure]:
    return [figure(workspace, key, platform, period) for key in (metrics or catalog.METRICS)]


# ── Published posts and breakdowns ───────────────────────────────────────────


@dataclass
class PostResult:
    id: str
    post_id: str
    title: str
    caption: str
    platform: str
    account: str
    published_at: str
    live_url: str
    thumbnail: str
    media_kind: str
    pillar: str
    format: str
    hook: str
    length: str
    time_of_day: str
    weekday: str
    metrics: dict[str, float | None]
    missing: dict[str, str]

    def as_dict(self) -> dict:
        return asdict(self)


def _length_bucket(seconds) -> str:
    if not seconds:
        return "Not a video"
    if seconds < 15:
        return "Under 15 s"
    if seconds < 30:
        return "15–30 s"
    if seconds < 60:
        return "30–60 s"
    return "Over a minute"


def _time_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "Morning (5–12)"
    if 12 <= hour < 17:
        return "Afternoon (12–17)"
    if 17 <= hour < 22:
        return "Evening (17–22)"
    return "Night (22–5)"


def published(workspace, period: Period, platform: str = "all") -> list[PostResult]:
    """Posts published in the period with their latest (lifetime to date) figures."""
    from apps.analytics.services import _latest_post_stats
    from apps.composer.models import PlatformPost
    from apps.studio.decorators import zone

    tz = zone(workspace.effective_timezone)
    start = timezone.make_aware(datetime.combine(period.start, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(period.end + timedelta(days=1), datetime.min.time()), tz)
    qs = (
        PlatformPost.objects.filter(
            post__workspace=workspace,
            status="published",
            published_at__gte=start,
            published_at__lt=end,
            social_account__platform__in=PLATFORMS,
        )
        .select_related("post__category", "social_account")
        .prefetch_related("post__media_attachments__media_asset")
        .order_by("-published_at")
    )
    if platform != "all":
        qs = qs.filter(social_account__platform=platform)
    rows = list(qs)
    keys = sorted({k for ks in catalog.POST_KEYS.values() for k in ks})
    stats = _latest_post_stats([pp.id for pp in rows], keys)
    plans = _plans([pp.post_id for pp in rows])
    out = []
    for pp in rows:
        values = stats.get(pp.id, {})
        metrics: dict[str, float | None] = {}
        missing: dict[str, str] = {}
        for metric, post_keys in catalog.POST_KEYS.items():
            src = catalog.source(metric, pp.social_account.platform)
            got = [values[k] for k in post_keys if k in values]
            if src.missing:
                metrics[metric], missing[metric] = None, str(src.missing)
            elif got:
                metrics[metric] = float(sum(got))
            else:
                metrics[metric], missing[metric] = None, "No figures yet"
        views = metrics.get("views")
        eng = metrics.get("engagement")
        metrics["engagement_rate"] = round(eng / views * 100, 2) if views and eng is not None else None
        if metrics["engagement_rate"] is None:
            missing["engagement_rate"] = missing.get("views") or "No views yet"
        media = [m.media_asset for m in pp.post.media_attachments.all()]
        first = media[0] if media else None
        plan = plans.get(pp.post_id)
        local = timezone.localtime(pp.published_at, tz)
        video = next((a for a in media if a.media_type == "video"), None)
        out.append(
            PostResult(
                id=str(pp.id),
                post_id=str(pp.post_id),
                title=pp.post.title or (pp.effective_caption or "")[:60] or "Untitled post",
                caption=(pp.effective_caption or "")[:280],
                platform=pp.social_account.platform,
                account=pp.social_account.account_name,
                published_at=pp.published_at.isoformat() if pp.published_at else "",
                live_url=(pp.platform_extra or {}).get("live_url", ""),
                thumbnail=_thumb(first),
                media_kind=(first.media_type if first else "text"),
                pillar=pp.post.category.name if pp.post.category else "No pillar",
                format=(plan.get_format_display() if plan and plan.format else _kind_label(media)),
                hook=(plan.hook if plan and plan.hook else ""),
                length=_length_bucket(getattr(video, "duration", None) if video else None),
                time_of_day=_time_bucket(local.hour),
                weekday=local.strftime("%A"),
                metrics=metrics,
                missing=missing,
            )
        )
    return out


def _plans(post_ids):
    from apps.planning.models import ContentPlan

    return {p.post_id: p for p in ContentPlan.objects.filter(post_id__in=post_ids)}


def _kind_label(media) -> str:
    if not media:
        return "Text"
    if len(media) > 1:
        return "Carousel"
    return "Video" if media[0].media_type == "video" else "Photo"


def _thumb(asset) -> str:
    if asset is None:
        return ""
    try:
        thumb = getattr(asset, "thumbnail", None)
        if thumb:
            return thumb.url
        return asset.file.url if asset.media_type == "image" and asset.file else ""
    except Exception:
        return ""


def rank(posts: list[PostResult], metric: str, limit: int = 10) -> list[PostResult]:
    scored = [p for p in posts if p.metrics.get(metric) is not None]
    return sorted(scored, key=lambda p: p.metrics[metric] or 0, reverse=True)[:limit]


BREAKDOWNS = (("pillar", "Pillar"), ("format", "Format"), ("hook", "Hook"), ("length", "Length"),
              ("time_of_day", "Posting time"))  # fmt: skip


def breakdowns(posts: list[PostResult], metric: str) -> list[dict]:
    """Average of ``metric`` per pillar, format, hook, length and posting time, with how many posts each."""
    out = []
    for attr, label in BREAKDOWNS:
        groups: dict[str, list[float]] = defaultdict(list)
        for p in posts:
            value = p.metrics.get(metric)
            if value is None:
                continue
            key = getattr(p, attr) or ("No hook written" if attr == "hook" else "—")
            groups[key].append(value)
        rows: list[dict[str, Any]] = [
            {"group": g, "posts": len(v), "average": round(sum(v) / len(v), 2), "small": len(v) < SMALL_SAMPLE}
            for g, v in sorted(groups.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        ]
        out.append({"by": attr, "label": label, "rows": rows[:6]})
    return out


# ── Goals ────────────────────────────────────────────────────────────────────


@dataclass
class GoalProgress:
    goal: Any
    period: Period
    actual: float | None
    target: float
    pct: float | None
    status: str  # met | on_track | behind | no_data
    figure: Figure

    @property
    def label(self) -> str:
        return {"met": "Met", "on_track": "On track", "behind": "Behind", "no_data": "No data yet"}[self.status]

    @property
    def tone(self) -> str:
        return {"met": "success", "on_track": "info", "behind": "warning", "no_data": "neutral"}[self.status]


def goal_period(goal, today: date | None = None) -> Period:
    today = today or timezone.localdate()
    if goal.period == "custom" and goal.starts_on and goal.ends_on:
        return Period(goal.starts_on, goal.ends_on)
    return month_period(today.year, today.month)


def goal_progress(goal, today: date | None = None, period: Period | None = None) -> GoalProgress:
    today = today or timezone.localdate()
    full = period or goal_period(goal, today)
    so_far = Period(full.start, min(full.end, today))
    fig = figure(goal.workspace, goal.metric, goal.platform, so_far)
    target = float(goal.target)
    if not fig.available or fig.value is None or so_far.end < so_far.start:
        return GoalProgress(goal, full, None, target, None, "no_data", fig)
    pct = fig.value / target * 100 if target else None
    if pct is not None and pct >= 100:
        status = "met"
    else:
        elapsed = so_far.days / full.days
        # Rates are judged on the level reached, counts on the pace so far.
        expected = 100 if catalog.metric(goal.metric).kind == "percent" else elapsed * 100
        status = "on_track" if pct is not None and pct >= expected * 0.9 else "behind"
    return GoalProgress(goal, full, fig.value, target, round(pct, 1) if pct is not None else None, status, fig)


def goals_progress(workspace, today: date | None = None, period: Period | None = None) -> list[GoalProgress]:
    from apps.reports.models import ClientGoal

    return [
        goal_progress(g, today, period)
        for g in ClientGoal.objects.filter(workspace=workspace, is_active=True).select_related("workspace")
    ]
