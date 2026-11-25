"""Drafting a report's summary: what happened, why it likely happened, what to do next.

Behind a replaceable adapter (``AI_PROVIDER``):

- ``mock`` (default): writes the draft from the report's figures with fixed
  sentences. No service is called; good for testing and demos.
- ``claude``: Anthropic's Messages API (``AI_MODEL``, key from the
  ``ANTHROPIC_API_KEY`` environment variable). Not tested against the live
  service in this repository.
- ``none``: drafting is switched off.

Either way the draft is only a starting point: the studio edits and approves
it before a client sees it, and the AI never approves or shares anything. The
writer is given only the report's own figures and told not to add others;
``check_numbers`` then lists any number in the draft that is not in them, so
the editor can see it before approving.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from django.conf import settings

log = logging.getLogger(__name__)


class DraftingError(Exception):
    """Drafting failed; the message is for the person."""


@dataclass
class Draft:
    what_happened: str
    why: str
    next_steps: str
    provider: str
    warnings: list[str] = field(default_factory=list)


# ── The facts a writer may use ───────────────────────────────────────────────


def facts(report) -> dict:
    """Everything the writer may say, taken from the report's own figures (nothing internal)."""
    data = report.data or {}
    overview = []
    for platform, figs in (data.get("overview") or {}).items():
        for f in figs:
            if f.get("available") and f.get("value") is not None:
                overview.append(
                    {
                        "platform": platform,
                        "metric": f["metric"],
                        "value": f["value"],
                        "previous_period": f.get("previous"),
                        "change_percent": f.get("change_pct"),
                        "combined_across_platforms": f.get("combined", False),
                        "calculated_by_studio": f.get("calculated", False),
                    }
                )
    return {
        "client": report.workspace.name,
        "period": data.get("period"),
        "posts_published": data.get("published_count", 0),
        "figures": overview,
        "top_posts": [
            {
                "title": p["title"],
                "platform": p["platform"],
                "format": p["format"],
                "pillar": p["pillar"],
                "metrics": {k: v for k, v in p["metrics"].items() if v is not None},
            }  # fmt: skip
            for p in (data.get("top_posts") or [])[:5]
        ],
        "breakdowns": [
            {"by": b["label"], "metric": data.get("rank_by"), "groups": b["rows"]}
            for b in (data.get("breakdowns") or [])
        ],
        "goals": data.get("goals", []),
    }


_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")


def _numbers_in(value, out: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        for form in (f"{value:,.0f}", f"{value:.0f}", f"{value:,.1f}", f"{value:.1f}", f"{value:,.2f}", f"{value:.2f}"):
            out.add(form.replace(",", ""))
        return
    if isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
    elif isinstance(value, list | tuple):
        for v in value:
            _numbers_in(v, out)
    elif isinstance(value, str):
        for match in _NUMBER.findall(value):
            out.add(match.rstrip("%").replace(",", ""))


def check_numbers(draft: Draft, fact_data: dict) -> list[str]:
    """Numbers in the draft that do not appear in the figures (years, list counts and small numbers aside)."""
    known: set[str] = set()
    _numbers_in(fact_data, known)
    found = []
    for text in (draft.what_happened, draft.why, draft.next_steps):
        for match in _NUMBER.findall(text or ""):
            plain = match.rstrip("%").replace(",", "")
            try:
                number = float(plain)
            except ValueError:
                continue
            if number <= 10 or 1990 <= number <= 2100:
                continue
            candidates = {plain, f"{number:.0f}", f"{number:.1f}", f"{number:.2f}"}
            if not candidates & known:
                found.append(match)
    return [f"Check this figure: {n} is not in the report's data." for n in dict.fromkeys(found)]


# ── Writers ──────────────────────────────────────────────────────────────────


def _fmt(value) -> str:
    return (
        f"{value:,.0f}"
        if isinstance(value, int | float) and abs(value) >= 100
        else f"{value:,.2f}".rstrip("0").rstrip(".")
    )


LABELS = {
    "en": {"views": "views", "engagement": "engagement", "audience_growth": "new followers",
           "unique_viewers": "unique viewers", "engagement_rate": "engagement rate"},
    "es": {"views": "reproducciones", "engagement": "interacciones", "audience_growth": "nuevos seguidores",
           "unique_viewers": "espectadores únicos", "engagement_rate": "tasa de interacción"},
}  # fmt: skip


class MockWriter:
    name = "mock"

    def draft(self, fact_data: dict, language: str = "en") -> Draft:
        es = language == "es"
        labels = LABELS["es" if es else "en"]
        figs = {(f["platform"], f["metric"]): f for f in fact_data["figures"]}
        lines = []
        for metric in ("views", "engagement", "audience_growth"):
            f = figs.get(("all", metric))
            if not f:
                continue
            text = f"{_fmt(f['value'])} {labels[metric]}"
            if f.get("change_percent") is not None:
                change = f["change_percent"]
                if es:
                    text += f" ({'+' if change >= 0 else ''}{_fmt(change)}% frente al periodo anterior)"
                else:
                    text += f" ({'+' if change >= 0 else ''}{_fmt(change)}% on the previous period)"
            lines.append(text)
        posts = fact_data.get("posts_published", 0)
        if es:
            what = f"Se publicaron {posts} piezas. " + ("; ".join(lines) + "." if lines else "")
        else:
            what = f"{posts} posts went out. " + ("; ".join(lines) + "." if lines else "")
        why_parts = []
        for b in fact_data.get("breakdowns", []):
            rows = [r for r in b["groups"] if not r["small"]]
            if len(rows) >= 2:
                best, worst = rows[0], rows[-1]
                if es:
                    why_parts.append(
                        f"Por {b['by'].lower()}: «{best['group']}» promedió {_fmt(best['average'])} "
                        f"({best['posts']} piezas) frente a {_fmt(worst['average'])} de «{worst['group']}» ({worst['posts']})."
                    )
                else:
                    why_parts.append(
                        f"By {b['by'].lower()}: “{best['group']}” averaged {_fmt(best['average'])} "
                        f"({best['posts']} posts) against {_fmt(worst['average'])} for “{worst['group']}” ({worst['posts']})."
                    )
        if not why_parts:
            why_parts.append(
                "Hay pocas piezas para comparar; tómelo como una hipótesis."
                if es
                else "There are too few posts to compare groups; treat any pattern as a hypothesis to test."
            )
        top = fact_data.get("top_posts") or []
        if es:
            nxt = (f"Repetir el enfoque de «{top[0]['title']}» ({top[0]['format']}, {top[0]['pillar']}) y medirlo el próximo mes."
                   if top else "Publicar con regularidad para tener datos que comparar.")  # fmt: skip
        else:
            nxt = (f"Make more like “{top[0]['title']}” ({top[0]['format']}, {top[0]['pillar']}) and measure it next month."
                   if top else "Post regularly so there is enough to compare next month.")  # fmt: skip
        behind = [g for g in fact_data.get("goals", []) if g.get("status") == "behind"]
        if behind:
            nxt += (" Objetivo atrasado: " if es else " Goal behind: ") + ", ".join(g["name"] for g in behind) + "."
        return Draft(what.strip(), " ".join(why_parts), nxt, self.name)


SYSTEM_PROMPT = """You draft the monthly social media report a creative studio sends its client.
Write three short sections for the client:
- what_happened: what the figures show for the period, compared with the previous period.
- why: why it likely happened, citing the breakdowns (format, pillar, hook, length, posting time) with their post counts.
  Say "likely". When a group has fewer than 3 posts, or there is no breakdown to point to, call it a hypothesis to test.
- next_steps: two or three concrete things to do next month, each tied to a figure.
Rules: use only the numbers in the JSON you are given. Never invent a figure, a percentage, a benchmark, a source or a trend.
Figures marked combined_across_platforms are totals of metrics each platform counts its own way; say so if you use them.
Do not mention internal notes, costs, margins or the studio's team. Plain, warm, specific language; no hype; no emoji.
Write in {language}."""

SCHEMA = {
    "type": "object",
    "properties": {
        "what_happened": {"type": "string"},
        "why": {"type": "string"},
        "next_steps": {"type": "string"},
    },
    "required": ["what_happened", "why", "next_steps"],
    "additionalProperties": False,
}


class ClaudeWriter:
    """Anthropic Messages API with a JSON schema for the three sections."""

    name = "claude"

    def __init__(self, model: str):
        self.model = model

    def draft(self, fact_data: dict, language: str = "en") -> Draft:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - the package is in requirements.txt
            raise DraftingError("The Anthropic SDK is not installed.") from exc
        client = anthropic.Anthropic()
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM_PROMPT.format(language="Spanish" if language == "es" else "English"),
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"},
                messages=[{"role": "user", "content": json.dumps(fact_data, ensure_ascii=False, default=str)}],
            )
        except anthropic.AuthenticationError as exc:
            raise DraftingError("The AI service rejected the API key. Check ANTHROPIC_API_KEY.") from exc
        except anthropic.RateLimitError as exc:
            raise DraftingError("The AI service is busy. Try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            log.warning("Report drafting failed: %s", exc)
            raise DraftingError("The AI service could not write a draft. Try again, or write it by hand.") from exc
        except anthropic.APIConnectionError as exc:
            raise DraftingError("Could not reach the AI service. Try again, or write it by hand.") from exc
        if response.stop_reason == "refusal":
            raise DraftingError("The AI service declined to write this draft. Write it by hand.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DraftingError("The AI service returned something unreadable. Try again.") from exc
        return Draft(data["what_happened"], data["why"], data["next_steps"], f"claude:{response.model}")


def writer():
    provider = getattr(settings, "AI_PROVIDER", "mock")
    if provider == "claude":
        return ClaudeWriter(getattr(settings, "AI_MODEL", "claude-opus-5-5"))
    if provider == "mock":
        return MockWriter()
    return None


def draft_for(report, language: str = "en") -> Draft:
    w = writer()
    if w is None:
        raise DraftingError("AI drafting is switched off (AI_PROVIDER=none). Write the summary by hand.")
    fact_data = facts(report)
    result = w.draft(fact_data, language)
    result.warnings = check_numbers(result, fact_data)
    return result
