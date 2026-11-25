"""Where "Emerging now" looks for trends, behind a replaceable adapter (``RESEARCH_PROVIDER``).

Only official APIs, licensed data providers, web search APIs and what the team
saves by hand. No scraping and no unofficial APIs.

- ``none`` (default): no automatic source. "Emerging now" is built from what the
  team saved to the swipe file (trends, hashtags, sounds), with a link out to
  TikTok Creative Center, which has no trends data API.
- ``mock``: three made-up trends, each labelled as test data, for trying the
  brief without a provider. Never use in production.

A licensed provider or a web search API is added as one more class with
``fetch(profile, since)``; every trend it returns must carry its source, link,
date and, where it has one, a sample size.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings

CREATIVE_CENTER_URL = "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en"


@dataclass
class Trend:
    title: str
    kind: str  # trend | hashtag | audio | format
    url: str
    source: str
    observed_on: date
    detail: str = ""
    sample_size: int | None = None
    expires_on: date | None = None
    is_test_data: bool = False


class NoSource:
    name = "none"
    label = "No automatic trend source"

    def fetch(self, profile, since: date) -> list[Trend]:
        return []


class MockSource:
    name = "mock"
    label = "Test data (not real trends)"

    def fetch(self, profile, since: date) -> list[Trend]:
        today = date.today()
        topic = (profile.lines("keywords") or [profile.industry or "your niche"])[0]
        return [
            Trend(f"TEST: “Day in the life” videos about {topic}", "format", "https://example.com/test-trend/1",
                  "Test data", today, "Made-up trend for trying the brief.", 40, today + timedelta(days=14), True),
            Trend("TEST: #SmallBusinessSaturday", "hashtag", "https://example.com/test-trend/2", "Test data", today,
                  "Made-up hashtag for trying the brief.", None, today + timedelta(days=7), True),
            Trend("TEST: a soft acoustic sound in before/after clips", "audio", "https://example.com/test-trend/3",
                  "Test data", today, "Made-up sound for trying the brief.", 12, today + timedelta(days=10), True),
        ]  # fmt: skip


def source():
    provider = getattr(settings, "RESEARCH_PROVIDER", "none")
    return MockSource() if provider == "mock" else NoSource()
