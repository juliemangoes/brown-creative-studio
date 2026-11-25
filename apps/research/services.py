"""The weekly research brief: three groups of recommendations, each with its evidence or labelled a hypothesis.

- **Working for this client**: patterns in the client's own published posts (last
  90 days) by format, pillar, hook, length and posting time, from
  ``apps.reports.figures``. The evidence is the posts themselves.
- **Working in this niche**: patterns in what the team saved to the swipe file
  from competitors and reference accounts (last 60 days).
- **Emerging now**: trends, hashtags and sounds the team saved (last 21 days)
  and, if one is configured, a trend source (``apps.research.sources``).

The numbers and sources come from code, never from the AI. The AI only writes
why a recommendation fits and a hook to try; that text is checked against the
client's topics to avoid and the evidence's own numbers.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.research import ai, sources
from apps.research.models import ResearchBrief, ResearchItem, ResearchProfile, SwipeItem

log = logging.getLogger(__name__)

OWN_DAYS = 90
NICHE_DAYS = 60
EMERGING_DAYS = 21
MIN_GROUP = 3  # posts per group before a comparison counts as evidence
OUTCOME_AFTER_DAYS = 7
OUTCOME_BASELINE_DAYS = 90
OUTCOME_MIN_BASELINE = 5


class ResearchError(ValueError):
    pass


def profile_for(workspace) -> ResearchProfile:
    profile, _ = ResearchProfile.objects.get_or_create(workspace=workspace)
    return profile


def week_start(day: date | None = None) -> date:
    day = day or timezone.localdate()
    return day - timedelta(days=day.weekday())


def confidence_for(sample: int) -> str:
    if sample >= 15:
        return ResearchItem.Confidence.HIGH
    if sample >= 8:
        return ResearchItem.Confidence.MEDIUM
    return ResearchItem.Confidence.LOW


# ── Working for this client ──────────────────────────────────────────────────


FORMAT_HINT = {"format": "{group}", "length": "Video, {group}", "time_of_day": "", "pillar": "", "hook": ""}


def _own(workspace, today: date) -> tuple[list[dict], list[str]]:
    from apps.reports import figures

    period = figures.Period(today - timedelta(days=OWN_DAYS - 1), today)
    posts = figures.published(workspace, period)
    metric = "engagement"
    with_metric = [p for p in posts if p.metrics.get(metric) is not None]
    if len(with_metric) < MIN_GROUP * 2:
        return [], [f"Working for this client: only {len(with_metric)} published posts with figures in the last "
                    f"{OWN_DAYS} days, too few to compare."]  # fmt: skip
    items = []
    for breakdown in figures.breakdowns(with_metric, metric):
        rows = [r for r in breakdown["rows"] if r["posts"] >= MIN_GROUP]
        if len(rows) < 2:
            continue
        best, rest = rows[0], rows[1:]
        baseline = rest[-1]
        if not baseline["average"] or best["average"] < baseline["average"] * 1.15:
            continue  # no real difference: nothing to recommend
        group_posts = [p for p in with_metric if (getattr(p, breakdown["by"]) or "No hook written") == best["group"]]
        top, seen = [], set()
        for p in sorted(group_posts, key=lambda p: p.metrics[metric] or 0, reverse=True):
            if p.post_id not in seen:  # one line per post, even when it went to two platforms
                seen.add(p.post_id)
                top.append(p)
        top = top[:3]
        sample = best["posts"] + baseline["posts"]
        label = breakdown["label"].lower()
        items.append(
            {
                "group": ResearchItem.Group.CLIENT,
                "title": f"More “{best['group']}” ({label})",
                "observed": (
                    f"Posts with {label} “{best['group']}” averaged {best['average']:,.0f} engagement over "
                    f"{best['posts']} posts, against {baseline['average']:,.0f} for “{baseline['group']}” "
                    f"({baseline['posts']} posts), in the last {OWN_DAYS} days."
                ),
                "evidence": [
                    {
                        "label": p.title,
                        "url": p.live_url,
                        "date": p.published_at[:10],
                        "source": f"{p.platform.title()}: {p.metrics[metric]:,.0f} engagement",
                    }  # fmt: skip
                    for p in top
                ],
                "sample_size": sample,
                "confidence": confidence_for(sample),
                "suggested_format": best["group"] if breakdown["by"] == "format" else "",
                "suggested_hook": best["group"]
                if breakdown["by"] == "hook" and best["group"] != "No hook written"
                else "",
                "expires_on": today + timedelta(days=30),
                "pattern": {"metric": metric, "by": breakdown["by"], "group": best["group"]},
            }
        )
    if not items:
        return [], [f"Working for this client: no group stood out by at least 15% in the last {OWN_DAYS} days."]
    return items[:4], []


# ── Working in this niche ────────────────────────────────────────────────────


def _niche(workspace, today: date) -> tuple[list[dict], list[str]]:
    saved = list(
        SwipeItem.objects.filter(
            workspace=workspace,
            whose__in=[SwipeItem.Whose.COMPETITOR, SwipeItem.Whose.REFERENCE],
            observed_on__gte=today - timedelta(days=NICHE_DAYS),
        )
    )
    if not saved:
        return [], ["Working in this niche: nothing saved from competitors or reference accounts in the last "
                    f"{NICHE_DAYS} days. Save examples to the swipe file."]  # fmt: skip
    items = []
    formats = Counter(s.format.strip().lower() for s in saved if s.format.strip())
    for fmt, count in formats.most_common(2):
        if count < 2:
            break
        examples = [s for s in saved if s.format.strip().lower() == fmt][:5]
        items.append(
            {
                "group": ResearchItem.Group.NICHE,
                "title": f"{fmt.title()} keeps coming up in the niche",
                "observed": f"{count} of the {len(saved)} examples saved from competitors and reference accounts in "
                f"the last {NICHE_DAYS} days are {fmt}.",
                "evidence": [_swipe_evidence(s) for s in examples],
                "sample_size": len(saved),
                "confidence": ResearchItem.Confidence.LOW if len(saved) < 10 else ResearchItem.Confidence.MEDIUM,
                "suggested_format": fmt.title(),
                "suggested_hook": next((s.hook for s in examples if s.hook), ""),
                "expires_on": today + timedelta(days=30),
                "pattern": {"metric": "engagement", "by": "format", "group": fmt.title()},
            }
        )
    hooks = [s for s in saved if s.hook]
    if hooks:
        best = hooks[0]
        items.append(
            {
                "group": ResearchItem.Group.NICHE,
                "title": f"Try a hook seen at {best.account or 'a competitor'}",
                "observed": f"Saved on {best.observed_on:%d %b}: “{best.hook}”"
                + (f" ({best.metric_note})" if best.metric_note else "")
                + ".",
                "evidence": [_swipe_evidence(best)],
                "sample_size": 1,
                "confidence": ResearchItem.Confidence.LOW,
                "suggested_format": best.format.title(),
                "suggested_hook": best.hook,
                "expires_on": best.expires_on or today + timedelta(days=21),
                "pattern": {"metric": "engagement"},
            }
        )
    if not items:
        return [], [f"Working in this niche: {len(saved)} saved examples, no repeated pattern yet."]
    return items, []


def _swipe_evidence(s: SwipeItem) -> dict:
    who = f"{s.account} · " if s.account else ""
    return {"label": s.title, "url": s.url, "date": s.observed_on.isoformat(),
            "source": f"{who}{s.source_name or s.get_whose_display()}" + (f" · {s.metric_note}" if s.metric_note else "")}  # fmt: skip


# ── Emerging now ─────────────────────────────────────────────────────────────


def _emerging(workspace, profile, today: date) -> tuple[list[dict], list[str], list[str]]:
    items, notes = [], []
    src = sources.source()
    used = [src.label]
    saved = SwipeItem.objects.filter(
        workspace=workspace,
        kind__in=[SwipeItem.Kind.TREND, SwipeItem.Kind.HASHTAG, SwipeItem.Kind.AUDIO, SwipeItem.Kind.FORMAT],
        observed_on__gte=today - timedelta(days=EMERGING_DAYS),
    ).exclude(expires_on__lt=today)
    for s in saved[:5]:
        items.append(
            {
                "group": ResearchItem.Group.EMERGING,
                "title": s.title,
                "observed": f"Saved by the team on {s.observed_on:%d %b} from {s.source_name or s.get_whose_display()}"
                + (f": {s.metric_note}" if s.metric_note else "")
                + ".",
                "evidence": [_swipe_evidence(s)],
                "sample_size": 1,
                "confidence": ResearchItem.Confidence.LOW,
                "suggested_format": s.format.title(),
                "suggested_hook": s.hook,
                "expires_on": s.expires_on or s.observed_on + timedelta(days=14),
                "pattern": {"metric": "engagement"},
            }
        )
    try:
        trends = src.fetch(profile, today - timedelta(days=EMERGING_DAYS))
    except Exception:
        log.exception("Trend source %s failed", src.name)
        trends = []
        notes.append(f"Emerging now: {src.label} could not be reached this week.")
    for t in trends[:5]:
        items.append(
            {
                "group": ResearchItem.Group.EMERGING,
                "title": t.title,
                "observed": t.detail or f"Listed by {t.source} on {t.observed_on:%d %b}.",
                "evidence": [{"label": t.title, "url": t.url, "date": t.observed_on.isoformat(), "source": t.source}],
                "sample_size": t.sample_size,
                "confidence": ResearchItem.Confidence.LOW,
                "suggested_format": t.kind.title() if t.kind == "format" else "",
                "expires_on": t.expires_on,
                "pattern": {"metric": "engagement"},
                "test_data": t.is_test_data,
            }
        )
    keywords = profile.lines("keywords") + profile.lines("hashtags")
    if keywords and not items:
        # Something to test even without evidence: explicitly a hypothesis.
        items.append(
            {
                "group": ResearchItem.Group.EMERGING,
                "title": f"Test a post around “{keywords[0]}”",
                "observed": "No trend evidence this week. This comes from the client's research profile, not from data.",
                "evidence": [],
                "sample_size": None,
                "confidence": ResearchItem.Confidence.HYPOTHESIS,
                "expires_on": today + timedelta(days=14),
                "pattern": {"metric": "engagement"},
            }
        )
    if not saved.exists() and not trends:
        notes.append(
            "Emerging now: nothing saved in the last three weeks. Check TikTok Creative Center and save what fits."
        )
    return items, notes, used


# ── The brief ────────────────────────────────────────────────────────────────


@transaction.atomic
def generate_brief(workspace, actor=None, today: date | None = None, *, use_ai: bool = True) -> ResearchBrief:
    today = today or timezone.localdate()
    profile = profile_for(workspace)
    brief = ResearchBrief.objects.create(workspace=workspace, week_start=week_start(today), generated_by=actor)
    own, own_notes = _own(workspace, today)
    niche, niche_notes = _niche(workspace, today)
    emerging, emerging_notes, used = _emerging(workspace, profile, today)
    brief.notes = own_notes + niche_notes + emerging_notes
    brief.sources_used = ["Your published posts (last 90 days)", "Swipe file", *used]
    context = ai.client_context(workspace)
    w = ai.writer() if use_ai else None
    for position, data in enumerate([*own, *niche, *emerging]):
        test_data = data.pop("test_data", False)
        item = ResearchItem(brief=brief, workspace=workspace, position=position, **data)
        if test_data:
            item.warnings = ["Test data from the mock trend source: not a real trend."]
        facts = ai.item_facts(item)  # before the AI adds anything
        if w is not None:
            try:
                suggestion = w.suggest(facts, context)
                item.why_fits = suggestion.why_fits
                item.suggested_hook = item.suggested_hook or suggestion.hook
                item.ai_drafted = True
                brief.ai_provider = suggestion.provider
            except ai.DraftingError as exc:
                item.warnings.append(str(exc))
        item.warnings += ai.checks([item.title, item.why_fits, item.suggested_hook], facts, context)
        item.save()
    brief.save(update_fields=["notes", "sources_used", "ai_provider"])
    record("research.brief", f"Research brief prepared for the week of {brief.week_start:%d %b}.", workspace=workspace,
           actor=actor, target=brief, data={"items": brief.items.count()})  # fmt: skip
    return brief


def latest_brief(workspace) -> ResearchBrief | None:
    return ResearchBrief.objects.filter(workspace=workspace).first()


# ── Turning a recommendation into content ────────────────────────────────────


def _mark_used(item, actor, **links):
    for key, value in links.items():
        setattr(item, key, value)
    item.status, item.used_by = ResearchItem.Status.USED, actor
    item.save()


def _description(item) -> str:
    lines = [item.observed]
    if item.why_fits:
        lines.append(f"Why it fits: {item.why_fits}")
    if item.suggested_hook:
        lines.append(f"Hook to try: {item.suggested_hook}")
    lines.append(
        f"Evidence: {'; '.join(e.get('url') or e.get('label', '') for e in item.evidence) or 'none (hypothesis to test)'}"
    )
    return "\n".join(lines)


@transaction.atomic
def to_idea(item, actor):
    from apps.composer.models import Idea

    if item.idea_id or item.post_id:
        raise ResearchError("This recommendation is already in the idea bank or the plan.")
    idea = Idea.objects.create(workspace=item.workspace, author=actor, title=item.title[:255], description=_description(item),
                               tags=["research"])  # fmt: skip
    _mark_used(item, actor, idea=idea)
    record("research.used", f"Recommendation saved as an idea: {item.title}.", workspace=item.workspace, actor=actor,
           target=idea)  # fmt: skip
    return idea


FORMATS = {"reel": "reel", "photo": "photo", "carousel": "carousel", "video": "video", "story": "story", "text": "text"}


@transaction.atomic
def to_plan(item, actor, target_date: date, *, draft_caption: bool = False):
    """A planned draft post (never scheduled, never approved): the normal review applies."""
    from apps.planning.services import quick_plan

    if item.post_id:
        raise ResearchError("This recommendation is already planned.")
    caption = ""
    warnings = []
    if draft_caption:
        w = ai.writer()
        if w is None:
            raise ResearchError("AI drafting is switched off (AI_PROVIDER=none).")
        context = ai.client_context(item.workspace)
        try:
            caption = w.caption(ai.item_facts(item), context, item.suggested_hook)
        except ai.DraftingError as exc:
            raise ResearchError(str(exc)) from exc
        warnings = ai.checks([caption], ai.item_facts(item), context)
    plan = quick_plan(
        item.workspace,
        actor,
        title=item.title,
        target_date=target_date,
        format=FORMATS.get((item.suggested_format or "").lower(), ""),
        caption=caption,
    )
    plan.hook = item.suggested_hook[:300]
    plan.save(update_fields=["hook"])
    if warnings:
        item.warnings = [*item.warnings, *(f"Caption: {w}" for w in warnings)]
    _mark_used(item, actor, post=plan.post)
    record("research.used", f"Recommendation planned for {target_date:%d %b}: {item.title}."
           + (" Caption drafted by AI for review." if caption else ""),
           workspace=item.workspace, actor=actor, target=plan.post)  # fmt: skip
    return plan


def dismiss(item, actor) -> None:
    item.status = ResearchItem.Status.DISMISSED
    item.save(update_fields=["status"])


# ── Learning from results ────────────────────────────────────────────────────


def _engagement(platform_posts) -> list[float]:
    from apps.analytics.services import _latest_post_stats

    keys = ["likes", "reactions", "comments", "shares", "saves"]
    stats = _latest_post_stats([pp.id for pp in platform_posts], keys)
    return [sum(stats[pp.id].values()) for pp in platform_posts if pp.id in stats]


def check_outcome(item, now=None) -> str:
    """How the content made from a recommendation did against the client's usual engagement per post."""
    from apps.composer.models import PlatformPost

    now = now or timezone.now()
    if not item.post_id:
        return ""
    made = list(PlatformPost.objects.filter(post_id=item.post_id, status="published", published_at__isnull=False))
    if not made:
        return ""
    first = min(pp.published_at for pp in made if pp.published_at)
    if first > now - timedelta(days=OUTCOME_AFTER_DAYS):
        return ""  # too early to judge
    result = _engagement(made)
    baseline_posts = list(
        PlatformPost.objects.filter(
            post__workspace=item.workspace,
            status="published",
            published_at__gte=first - timedelta(days=OUTCOME_BASELINE_DAYS),
            published_at__lt=first,
        ).exclude(post_id=item.post_id)
    )
    baseline = _engagement(baseline_posts)
    if not result or len(baseline) < OUTCOME_MIN_BASELINE:
        item.outcome, item.outcome_detail = "", {"reason": "Not enough earlier posts to compare with."}
    else:
        value, usual = sum(result) / len(result), statistics.median(baseline)
        item.outcome = "above" if value > usual * 1.1 else "below" if value < usual * 0.9 else "typical"
        item.outcome_detail = {"engagement": round(value), "usual": round(usual), "compared_with": len(baseline),
                               "metric": "engagement per post"}  # fmt: skip
    item.outcome_checked_at = now
    item.save(update_fields=["outcome", "outcome_detail", "outcome_checked_at"])
    return item.outcome


def update_outcomes(now=None) -> int:
    count = 0
    for item in ResearchItem.objects.filter(status=ResearchItem.Status.USED, post__isnull=False, outcome=""):
        if check_outcome(item, now):
            count += 1
    return count


def track_record(workspace) -> list[dict]:
    """Which advice worked: per group, how many judged recommendations beat the client's usual."""
    rows = []
    judged = ResearchItem.objects.filter(workspace=workspace).exclude(outcome="")
    for value, label in ResearchItem.Group.choices:
        items = [i for i in judged if i.group == value]
        above = sum(1 for i in items if i.outcome == "above")
        rows.append({"group": label, "judged": len(items), "above": above,
                     "typical": sum(1 for i in items if i.outcome == "typical"),
                     "below": sum(1 for i in items if i.outcome == "below")})  # fmt: skip
    hypotheses = [i for i in judged if i.is_hypothesis]
    rows.append({"group": "Hypotheses (any group)", "judged": len(hypotheses),
                 "above": sum(1 for i in hypotheses if i.outcome == "above"),
                 "typical": sum(1 for i in hypotheses if i.outcome == "typical"),
                 "below": sum(1 for i in hypotheses if i.outcome == "below")})  # fmt: skip
    return rows


# ── Weekly run ───────────────────────────────────────────────────────────────


def run_weekly(now=None) -> int:
    from django.conf import settings

    from apps.studio.decorators import zone
    from apps.workspaces.models import Workspace

    now = now or timezone.now()
    today = timezone.localtime(now, zone(None)).date()
    made = 0
    if today.weekday() == int(getattr(settings, "RESEARCH_BRIEF_WEEKDAY", 0)):
        for ws in Workspace.objects.filter(is_archived=False, client_profile__stage__in=["active", "onboarding"]):
            if not ResearchBrief.objects.filter(workspace=ws, week_start=week_start(today)).exists():
                try:
                    generate_brief(ws, today=today)
                    made += 1
                except Exception:
                    log.exception("Weekly research brief for %s failed", ws.pk)
    update_outcomes(now)
    return made
