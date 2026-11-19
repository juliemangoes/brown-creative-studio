"""Shared status vocabulary: every status is a word plus a tone, never colour alone."""

from django import template

register = template.Library()

# tone: neutral | accent | info | success | warning | danger
STATUS_TONES: dict[str, dict[str, tuple[str, str]]] = {
    # BrightBean's PlatformPost editorial statuses (apps.composer.models.PlatformPost.Status)
    "post": {
        "draft": ("Draft", "neutral"),
        "pending_review": ("Pending review", "warning"),
        "pending_client": ("Pending client", "warning"),
        "approved": ("Approved", "success"),
        "changes_requested": ("Changes requested", "warning"),
        "rejected": ("Rejected", "danger"),
        "scheduled": ("Scheduled", "info"),
        "publishing": ("Publishing", "info"),
        "published": ("Published", "success"),
        "failed": ("Failed", "danger"),
        "on_hold": ("On hold", "neutral"),
        "manual_action_needed": ("Manual action needed", "warning"),
    },
    # Production stage, tracked separately in apps.planning (Phase 4)
    "production": {
        "idea": ("Idea", "neutral"),
        "planned": ("Planned", "accent"),
        "shot_needed": ("Shot needed", "warning"),
        "in_production": ("In production", "info"),
        "ready_for_review": ("Ready for review", "success"),
    },
    # Invoices (apps.billing, Phase 5)
    "invoice": {
        "draft": ("Draft", "neutral"),
        "sent": ("Sent", "info"),
        "viewed": ("Viewed", "info"),
        "partly_paid": ("Partly paid", "warning"),
        "paid": ("Paid", "success"),
        "overdue": ("Overdue", "danger"),
        "void": ("Void", "neutral"),
    },
    # Client pipeline (apps.clients, Phase 2)
    "pipeline": {
        "lead": ("Lead", "neutral"),
        "proposal_sent": ("Proposal sent", "info"),
        "onboarding": ("Onboarding", "accent"),
        "active": ("Active", "success"),
        "paused": ("Paused", "warning"),
        "former": ("Former", "neutral"),
    },
    # Tasks and projects (apps.projects, Phase 3)
    "task": {
        "todo": ("To do", "neutral"),
        "in_progress": ("In progress", "info"),
        "waiting_client": ("Waiting on client", "warning"),
        "done": ("Done", "success"),
    },
    "project": {
        "planned": ("Planned", "neutral"),
        "active": ("In progress", "info"),
        "on_hold": ("On hold", "warning"),
        "done": ("Done", "success"),
        "cancelled": ("Cancelled", "neutral"),
    },
    # Brand stages (apps.brand) and onboarding steps (apps.client_onboarding), Phase 3
    "brand_stage": {
        "not_started": ("Not started", "neutral"),
        "in_progress": ("In progress", "info"),
        "awaiting_signoff": ("Waiting for sign-off", "warning"),
        "changes_requested": ("Changes requested", "warning"),
        "signed_off": ("Signed off", "success"),
    },
    "onboarding": {
        "todo": ("To do", "neutral"),
        "in_progress": ("In progress", "info"),
        "waiting_studio": ("With the studio", "accent"),
        "done": ("Done", "success"),
        "skipped": ("Skipped", "neutral"),
        "blocked": ("Blocked", "danger"),
    },
}


@register.inclusion_tag("ds/_status.html")
def status(kind: str, value: str, label: str = ""):
    """``{% status "post" platform_post.status %}`` renders the labelled, toned status."""
    default_label, tone = STATUS_TONES.get(kind, {}).get(value, (value.replace("_", " ").capitalize(), "neutral"))
    return {"label": label or default_label, "tone": tone}
