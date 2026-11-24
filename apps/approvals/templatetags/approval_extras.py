"""Template helpers for the approval-workflow UI."""

from django import template

register = template.Library()

# Presentation metadata per editorial status (dot / background / foreground /
# border + label), kept here so every approval surface (publish tab, composer
# panel, client portal) renders an identical badge from one source.
#
# Brown Creative Studio (CHANGES.md): values are design tokens, so badges follow
# the light and dark themes. Colour carries meaning (warning = waiting on
# someone, info = scheduled or in flight, success = approved or live, danger =
# rejected or failed, neutral = parked), and the label always says it in words.


def _meta(label, tone):
    if tone == "neutral":
        return {
            "label": label,
            "dot": "var(--text-secondary)",
            "bg": "var(--sunken)",
            "fg": "var(--text-secondary)",
            "bd": "var(--hairline)",
        }
    return {
        "label": label,
        "dot": f"var(--{tone})",
        "bg": f"var(--{tone}-soft)",
        "fg": f"var(--{tone})",
        "bd": f"color-mix(in srgb, var(--{tone}) 30%, transparent)",
    }


STATUS_META = {
    "draft": _meta("Draft", "neutral"),
    "pending_review": _meta("Pending review", "warning"),
    "pending_client": _meta("Pending client", "warning"),
    "approved": _meta("Approved", "success"),
    "changes_requested": _meta("Changes requested", "warning"),
    "rejected": _meta("Rejected", "danger"),
    "scheduled": _meta("Scheduled", "info"),
    "publishing": _meta("Publishing", "info"),
    "published": _meta("Published", "success"),
    "partially_published": _meta("Partially published", "warning"),
    "failed": _meta("Failed", "danger"),
    "on_hold": _meta("Hold requested", "neutral"),
    "manual_action": _meta("Manual action needed", "warning"),
}


@register.inclusion_tag("approvals/partials/_status_badge.html")
def status_badge(status):
    """Render the editorial-status pill (dot + label) for *status*."""
    return {"meta": STATUS_META.get(status, STATUS_META["draft"])}
