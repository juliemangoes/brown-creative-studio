"""Turns a report's stored figures into what a page or PDF shows, in the reader's language.

The same function feeds the studio's preview, the portal, the share link and
the PDF, so all four show exactly the same thing. It reads only the frozen
``data`` and the three summary fields; ``internal_notes`` is never touched.
"""

from __future__ import annotations

from datetime import date, datetime

from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.reports import catalog

# Fixed strings stored in report data in English, translated when shown.
_STORED = (
    gettext_noop("Calculated by the studio from the platform's running totals."),
    gettext_noop("Total across platforms, which each count this their own way."),
    gettext_noop("Not added up across platforms: someone who saw two accounts would count twice."),
    gettext_noop("Includes figures imported from a file."),
    gettext_noop("No comparison: there is too little data for the previous period."),
    gettext_noop("No account connected"),
    gettext_noop("Not available for these accounts"),
    gettext_noop("No data for this period yet"),
    gettext_noop("No views in this period"),
    gettext_noop("No figures yet"),
    gettext_noop("No views yet"),
    gettext_noop("Pillar"),
    gettext_noop("Format"),
    gettext_noop("Hook"),
    gettext_noop("Length"),
    gettext_noop("Posting time"),
    gettext_noop("No pillar"),
    gettext_noop("No hook written"),
    gettext_noop("Not a video"),
    gettext_noop("Under 15 s"),
    gettext_noop("Over a minute"),
    gettext_noop("Morning (5–12)"),
    gettext_noop("Afternoon (12–17)"),
    gettext_noop("Evening (17–22)"),
    gettext_noop("Night (22–5)"),
    gettext_noop("Photo"),
    gettext_noop("Video"),
    gettext_noop("Carousel"),
    gettext_noop("Text"),
    gettext_noop("Met"),
    gettext_noop("On track"),
    gettext_noop("Behind"),
    gettext_noop("No data yet"),
)

HEADLINE = ("views", "unique_viewers", "engagement", "engagement_rate", "audience_growth")
TONE = {"met": "success", "on_track": "info", "behind": "warning", "no_data": "neutral"}


def tr(text: str) -> str:
    """Translate a stored English string; "Platform: message" is translated by parts."""
    if not text:
        return ""
    if text.endswith(" only.") and text[: -len(" only.")] in ("Facebook", "Instagram", "TikTok"):
        return _("%(platform)s only.") % {"platform": text[: -len(" only.")]}
    if ": " in text:
        head, _sep, rest = text.partition(": ")
        if head in catalog.PLATFORM_LABELS.values() or head in ("Facebook", "Instagram", "TikTok"):
            return f"{head}: {_(rest)}"
    return _(text)


def number(value, kind: str = "count") -> str:
    if value is None:
        return ""
    if kind == "percent":
        return f"{formats.number_format(round(value, 2), decimal_pos=1, use_l10n=True)}%"
    return formats.number_format(round(value), decimal_pos=0, use_l10n=True, force_grouping=True)


def change(pct) -> str:
    if pct is None:
        return ""
    sign = "+" if pct > 0 else ""
    return f"{sign}{formats.number_format(pct, decimal_pos=1, use_l10n=True)}%"


def when(iso: str) -> str:
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return formats.date_format(timezone.localtime(moment), "j M Y, H:i")


def day(iso: str) -> str:
    try:
        return formats.date_format(date.fromisoformat(iso), "j M Y")
    except (TypeError, ValueError):
        return ""


def figure_view(f: dict) -> dict:
    m = catalog.METRICS.get(f["metric"])
    kind = m.kind if m else "count"
    return {
        "metric": f["metric"],
        "label": str(m.label) if m else f["metric"],
        "definition": str(m.definition) if m else "",
        "available": f.get("available") and f.get("value") is not None,
        "value": number(f.get("value"), kind),
        "raw": f.get("value"),
        "previous": number(f.get("previous"), kind),
        "change": change(f.get("change_pct")),
        "change_pct": f.get("change_pct"),
        "missing": tr(f.get("missing", "")),
        "sources": f.get("sources", []),
        "updated": when(f.get("updated_at", "")),
        "notes": [tr(n) for n in f.get("notes", [])],
        "calculated": f.get("calculated", False),
        "imported": f.get("imported", False),
        "combined": f.get("combined", False),
        "series": f.get("series", []),
    }


def chart(series: list) -> dict:
    """An SVG polyline for a daily series (gaps break the line)."""
    values = [v for _d, v in series if v is not None]
    if len(values) < 2:
        return {}
    top = max(values) or 1
    width, height = 300, 64
    step = width / max(len(series) - 1, 1)
    segments: list[str] = []
    current: list[str] = []
    for i, (_d, v) in enumerate(series):
        if v is None:
            if len(current) > 1:
                segments.append(" ".join(current))
            current = []
            continue
        current.append(f"{i * step:.1f},{height - (v / top) * (height - 4) - 2:.1f}")
    if len(current) > 1:
        segments.append(" ".join(current))
    return {"segments": segments, "width": width, "height": height, "first": day(series[0][0]),
            "last": day(series[-1][0])}  # fmt: skip


def present(report) -> dict:
    data = report.data or {}
    overview = data.get("overview") or {}
    combined = {f["metric"]: figure_view(f) for f in overview.get("all", [])}
    headline = [combined[k] for k in HEADLINE if k in combined]
    platforms = []
    for platform in data.get("platforms", []):
        rows = [figure_view(f) for f in overview.get(platform, [])]
        views = next((r for r in rows if r["metric"] == "views"), None)
        platforms.append(
            {
                "key": platform,
                "label": str(catalog.PLATFORM_LABELS.get(platform, platform)),
                "rows": rows,
                "chart": chart(views["series"]) if views else {},
            }  # fmt: skip
        )
    rank_by = data.get("rank_by", "engagement")
    rank_metric = catalog.METRICS.get(rank_by)
    posts = []
    for p in data.get("top_posts", []):
        posts.append(
            {
                **p,
                "platform_label": str(catalog.PLATFORM_LABELS.get(p["platform"], p["platform"])),
                "published": when(p.get("published_at", "")),
                "format_label": _(p.get("format", "")),
                "pillar_label": _(p.get("pillar", "")),
                "figures": [
                    {
                        "label": str(catalog.METRICS[k].label),
                        "value": number(p["metrics"].get(k), catalog.METRICS[k].kind)
                        if p["metrics"].get(k) is not None
                        else "",
                        "missing": tr(p.get("missing", {}).get(k, "")),
                    }  # fmt: skip
                    for k in ("views", "engagement", "engagement_rate", "shares", "saves")
                ],
            }
        )
    breakdowns = []
    for b in data.get("breakdowns", []):
        rows = [
            {**r, "group": _(r["group"]), "average": number(r["average"], rank_metric.kind if rank_metric else "count")}
            for r in b["rows"]
        ]
        if rows:
            breakdowns.append({"label": _(b["label"]), "rows": rows})
    goals = []
    for g in data.get("goals", []):
        m = catalog.METRICS.get(g["metric"])
        kind = m.kind if m else "count"
        goals.append(
            {
                **g,
                "label": _(g["label"]),
                "tone": TONE.get(g["status"], "neutral"),
                "metric_label": str(m.label) if m else g["metric"],
                "platform_label": str(catalog.PLATFORM_LABELS.get(g["platform"], g["platform"])),
                "actual_display": number(g.get("actual"), kind) if g.get("actual") is not None else "",
                "target_display": number(g.get("target"), kind),
                "bar": min(100, max(0, g.get("pct") or 0)),
            }
        )
    period = data.get("period") or {}
    previous = data.get("previous") or {}
    return {
        "period_label": f"{day(period.get('start', ''))} – {day(period.get('end', ''))}" if period else "",
        "previous_label": f"{day(previous.get('start', ''))} – {day(previous.get('end', ''))}" if previous else "",
        "headline": headline,
        "platform_tables": platforms,
        "top_posts": posts,
        "rank_label": str(rank_metric.label) if rank_metric else rank_by,
        "breakdowns": breakdowns,
        "goals": goals,
        "published_count": data.get("published_count", 0),
        "computed": when(data.get("computed_at", "")),
        "summary": [
            (_("What happened"), report.what_happened),
            (_("Why it likely happened"), report.why),
            (_("What we'll do next"), report.next_steps),
        ],
    }
