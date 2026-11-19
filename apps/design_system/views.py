"""The living style guide: every shared component, in the current theme, with its markup."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.design_system.templatetags.ds import STATUS_TONES

COLOUR_TOKENS = [
    ("canvas", "App background"),
    ("surface", "Panels, tables, dialogs"),
    ("sunken", "Wells, hover, selected rows"),
    ("hairline", "Dividers and quiet borders"),
    ("control-border", "Inputs and interactive outlines"),
    ("text-primary", "Main text"),
    ("text-secondary", "Labels and supporting text"),
    ("accent", "Primary actions, focus, active navigation"),
    ("on-accent", "Text on accent"),
    ("success", "Approved, published, paid"),
    ("warning", "Due soon, needs attention"),
    ("danger", "Failed, overdue, destructive"),
    ("info", "Scheduled, in progress"),
]
TYPE_SCALE = [44, 32, 24, 20, 16, 14, 13, 12]


@login_required
@require_GET
def style_guide(request):
    # Studio staff only: an organization member, never a client on a portal session.
    if request.session.get("is_portal_session") or getattr(request, "org_membership", None) is None:
        raise PermissionDenied
    return render(
        request,
        "design_system/style_guide.html",
        {"colour_tokens": COLOUR_TOKENS, "type_scale": TYPE_SCALE, "status_tones": STATUS_TONES},
    )
