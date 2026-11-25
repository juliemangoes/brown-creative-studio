"""AI help in research: why a recommendation fits the client, a hook to try, and a first caption.

Same adapter as report drafts (``AI_PROVIDER``: mock, claude or none). The writer
gets the recommendation's own evidence and the client's profile and brand voice,
nothing else, and is told not to invent figures, sources or trends. Its text is
then checked: numbers not in the evidence and topics the client avoids are
flagged on the recommendation. AI never approves, schedules or publishes; a
drafted caption lands on a draft post that goes through review like any other.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

log = logging.getLogger(__name__)


class DraftingError(Exception):
    pass


@dataclass
class Suggestion:
    why_fits: str
    hook: str
    provider: str
    warnings: list[str] = field(default_factory=list)


def client_context(workspace) -> dict:
    from apps.brand.services import profile_for as brand_for
    from apps.research.services import profile_for

    research = profile_for(workspace)
    brand = brand_for(workspace)
    return {
        "client": workspace.name,
        "industry": research.industry,
        "location": research.location,
        "language": research.language,
        "audience": research.audience,
        "voice": brand.voice,
        "voice_do": brand.voice_do,
        "voice_dont": brand.voice_dont,
        "topics_to_avoid": research.lines("topics_to_avoid")
        + [t.strip() for t in (brand.content_avoid or "").splitlines() if t.strip()],
    }


def item_facts(item) -> dict:
    return {
        "group": item.get_group_display(),
        "title": item.title,
        "observed": item.observed,
        "evidence": item.evidence,
        "sample_size": item.sample_size,
        "confidence": item.get_confidence_display(),
        "suggested_format": item.suggested_format,
        # A hook here came from the evidence (the swipe file), not from the AI.
        "suggested_hook": item.suggested_hook,
    }


def avoided_topics(text: str, topics: list[str]) -> list[str]:
    found = []
    low = (text or "").lower()
    for topic in topics:
        t = topic.lower().strip()
        if t and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", low):
            found.append(topic)
    return found


def checks(texts: list[str], facts: dict, context: dict) -> list[str]:
    from apps.reports.ai import Draft, check_numbers

    known = {"evidence": facts, "client": {k: v for k, v in context.items() if k != "topics_to_avoid"}}
    warnings = [
        w.replace("the report's data", "the evidence or the client profile")
        for w in check_numbers(Draft(" ".join(texts), "", "", ""), known)
    ]
    for topic in avoided_topics(" ".join(texts), context["topics_to_avoid"]):
        warnings.append(f"Mentions “{topic}”, which this client avoids.")
    return warnings


class MockWriter:
    name = "mock"

    def suggest(self, facts: dict, context: dict) -> Suggestion:
        audience = context["audience"].split(".")[0].strip()
        why = f"Fits {context['client']}'s audience" + (f" ({audience[0].lower()}{audience[1:]})" if audience else "")
        why += ", based on the evidence above."
        if facts["confidence"] == "Hypothesis to test":
            why = f"Untested for {context['client']}: worth one post to find out, then compare with the usual."
        fmt = (facts["suggested_format"] or "").lower()
        hook = "Open with the result first, then show how" + (f" in a {fmt}." if fmt else ".")
        return Suggestion(why, hook, self.name)

    def caption(self, facts: dict, context: dict, hook: str) -> str:
        return f"{hook}\n\n[Draft caption for {context['client']}: write in the brand voice, then review.]"


SYSTEM = """You help a creative studio plan social media content for one client.
Given one research recommendation (with its evidence) and the client's profile, write:
- why_fits: one or two sentences on why this suits this client, based only on the evidence and profile.
- hook: one opening line to try, in the client's voice.
Rules: never invent a figure, a source, a trend or a result. If the recommendation is a hypothesis, say it is untested.
Never mention the client's topics to avoid. Plain, specific, no hype, no emoji. Write in {language}."""

CAPTION_SYSTEM = """You draft a first social media caption for a creative studio's client, for a person to review and edit.
Use the hook given, the client's voice and the recommendation. Never invent facts, prices, offers, figures or claims.
Never mention the client's topics to avoid. Under 80 words, no hashtags unless the evidence names them. Write in {language}."""

SCHEMA = {
    "type": "object",
    "properties": {"why_fits": {"type": "string"}, "hook": {"type": "string"}},
    "required": ["why_fits", "hook"],
    "additionalProperties": False,
}


class ClaudeWriter:
    name = "claude"

    def __init__(self, model: str):
        self.model = model

    def _call(self, system: str, payload: dict, schema: dict | None):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise DraftingError("The Anthropic SDK is not installed.") from exc
        client = anthropic.Anthropic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
            "system": system,
            "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
            "output_config": {
                "effort": "low",
                **({"format": {"type": "json_schema", "schema": schema}} if schema else {}),
            },
        }
        try:
            response = client.beta.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            log.warning("Research drafting failed: %s", exc)
            raise DraftingError("The AI service could not write this. Try again, or write it by hand.") from exc
        except anthropic.APIConnectionError as exc:
            raise DraftingError("Could not reach the AI service. Try again, or write it by hand.") from exc
        if response.stop_reason == "refusal":
            raise DraftingError("The AI service declined. Write it by hand.")
        return next((b.text for b in response.content if b.type == "text"), ""), response.model

    def suggest(self, facts: dict, context: dict) -> Suggestion:
        text, model = self._call(SYSTEM.format(language=context["language"] or "English"),
                                 {"recommendation": facts, "client": context}, SCHEMA)  # fmt: skip
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DraftingError("The AI service returned something unreadable.") from exc
        return Suggestion(data["why_fits"], data["hook"][:300], f"claude:{model}")

    def caption(self, facts: dict, context: dict, hook: str) -> str:
        text, _model = self._call(CAPTION_SYSTEM.format(language=context["language"] or "English"),
                                  {"recommendation": facts, "client": context, "hook": hook}, None)  # fmt: skip
        return text.strip()


def writer():
    provider = getattr(settings, "AI_PROVIDER", "mock")
    if provider == "claude":
        return ClaudeWriter(getattr(settings, "AI_MODEL", "claude-opus-5-5"))
    if provider == "mock":
        return MockWriter()
    return None
