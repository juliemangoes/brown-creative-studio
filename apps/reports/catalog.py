"""The metrics the studio reports, with what each means and where it comes from on each platform.

Every figure on a dashboard, report or PDF carries its definition, its source
and when it was last updated. A platform that does not provide a metric says
"Not available from this platform"; one that provides it but we do not read
yet says "Not collected yet". Nothing is estimated silently: figures we build
ourselves (daily TikTok views from per-video totals, audience growth from
follower totals) are labelled as calculated.

Rules from the brief (checked 2026-11-18):
- Instagram: build on views, never impressions or plays (deprecated).
- Facebook: unique viewers come from the *_total_media_view_unique metrics that
  replaced reach on 2026-06-15; the two are not one line (see BREAKS).
- TikTok: per-video counts are lifetime totals; daily figures are our
  calculation from day-to-day differences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.utils.translation import gettext_lazy as _

PLATFORMS = ("facebook", "instagram", "tiktok")
PLATFORM_LABELS = {"facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok", "all": _("All platforms")}

NOT_FROM_PLATFORM = _("Not available from this platform")
NOT_COLLECTED = _("Not collected yet")


@dataclass(frozen=True)
class Source:
    """How one platform provides one metric."""

    keys: tuple[str, ...] = ()  # BrightBean snapshot metric_key(s), summed
    field: str = ""  # the platform's own name for it
    how: str = "daily"  # daily: sum of daily account figures; total: change in a running total; posts: from per-post lifetime totals
    missing: Any = ""  # set when the platform does not give it: NOT_FROM_PLATFORM or NOT_COLLECTED


@dataclass(frozen=True)
class Metric:
    key: str
    label: object
    definition: object
    kind: str = "count"  # count | percent
    # Platforms count these differently, so a total across platforms is labelled as such.
    combine: str = "sum_labelled"  # sum_labelled | never
    sources: dict[str, Source] = field(default_factory=dict)


METRICS: dict[str, Metric] = {
    m.key: m
    for m in (
        Metric(
            "audience_growth",
            _("Audience growth"),
            _("New followers in the period, minus those who left where the platform reports it."),
            sources={
                "instagram": Source(("followers",), "followers_count", "total"),
                "facebook": Source(("follows",), "page_daily_follows_unique"),
                "tiktok": Source(missing=NOT_COLLECTED),
            },
        ),
        Metric(
            "views",
            _("Views"),
            _("How many times the content was seen, counting repeat views."),
            sources={
                "instagram": Source(("views",), "views"),
                "facebook": Source(("views",), "page_media_view"),
                "tiktok": Source(("views",), "view_count", "posts"),
            },
        ),
        Metric(
            "unique_viewers",
            _("Unique viewers"),
            _("How many different accounts saw the content. People can't be added up across platforms."),
            combine="never",
            sources={
                "instagram": Source(("reach",), "reach"),
                "facebook": Source(("reach",), "page_total_media_view_unique"),
                "tiktok": Source(missing=NOT_FROM_PLATFORM),
            },
        ),
        Metric(
            "engagement",
            _("Engagement"),
            _("Likes or reactions, comments, shares and saves, where the platform reports each."),
            sources={
                "instagram": Source(("likes", "comments", "shares", "saves"), "likes + comments + shares + saved"),
                "facebook": Source(("reactions", "comments", "shares"), "reactions + comments + shares"),
                "tiktok": Source(("likes", "comments", "shares"), "like_count + comment_count + share_count", "posts"),
            },
        ),
        Metric(
            "engagement_rate",
            _("Engagement rate"),
            _("Engagement divided by views, as a percentage."),
            kind="percent",
            sources={p: Source(field="engagement / views") for p in PLATFORMS},
        ),
        Metric(
            "comments",
            _("Comments"),
            _("Comments on the content."),
            sources={
                "instagram": Source(("comments",), "comments"),
                "facebook": Source(("comments",), "comments"),
                "tiktok": Source(("comments",), "comment_count", "posts"),
            },
        ),
        Metric(
            "shares",
            _("Shares"),
            _("Times the content was shared or sent on."),
            sources={
                "instagram": Source(("shares",), "shares"),
                "facebook": Source(("shares",), "shares"),
                "tiktok": Source(("shares",), "share_count", "posts"),
            },
        ),
        Metric(
            "saves",
            _("Saves"),
            _("Times people saved the content to come back to."),
            sources={
                "instagram": Source(("saves",), "saved"),
                "facebook": Source(missing=NOT_FROM_PLATFORM),
                "tiktok": Source(missing=NOT_COLLECTED),
            },
        ),
        Metric(
            "link_clicks",
            _("Link clicks"),
            _("Clicks on links in the content."),
            sources={
                "instagram": Source(missing=NOT_FROM_PLATFORM),
                "facebook": Source(("clicks",), "post_clicks"),
                "tiktok": Source(missing=NOT_FROM_PLATFORM),
            },
        ),
        Metric(
            "profile_activity",
            _("Profile activity"),
            _("Visits to the profile and taps on its buttons."),
            sources={
                "instagram": Source(missing=NOT_COLLECTED),
                "facebook": Source(missing=NOT_COLLECTED),
                "tiktok": Source(missing=NOT_FROM_PLATFORM),
            },
        ),
        Metric(
            "video_retention",
            _("Video retention"),
            _("How much of a video people watched, on average."),
            kind="percent",
            combine="never",
            sources={
                "instagram": Source(missing=NOT_COLLECTED),
                "facebook": Source(missing=NOT_COLLECTED),
                "tiktok": Source(missing=NOT_FROM_PLATFORM),
            },
        ),
    )
}

# Metrics a goal can be set on, and that content can be ranked by.
GOAL_METRICS = ("audience_growth", "views", "unique_viewers", "engagement", "engagement_rate", "shares", "saves",
                "link_clicks", "comments")  # fmt: skip
RANK_METRICS = ("views", "engagement", "engagement_rate", "shares", "saves", "comments")

# Per-post metric keys stored by BrightBean (lifetime totals per post).
POST_KEYS = {
    "views": ("views",),
    "unique_viewers": ("reach",),
    "engagement": ("likes", "reactions", "comments", "shares", "saves"),
    "comments": ("comments",),
    "shares": ("shares",),
    "saves": ("saves",),
    "link_clicks": ("clicks",),
}

# Where a platform changed what a metric means: figures on either side are not one line.
BREAKS: dict[tuple[str, str], tuple[date, object]] = {
    ("facebook", "unique_viewers"): (
        date(2026, 6, 15),
        _(
            "Meta replaced Page reach with unique media viewers on 15 June 2026. Figures before and after are not comparable."
        ),
    ),
}


def metric(key: str) -> Metric:
    return METRICS[key]


def source(key: str, platform: str) -> Source:
    return METRICS[key].sources.get(platform, Source(missing=NOT_FROM_PLATFORM))
