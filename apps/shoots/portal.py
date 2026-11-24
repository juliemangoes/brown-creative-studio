"""Shoots in the client portal: dates, the preparation checklist (the client can tick it) and the call sheet.

Needs ``portal_view_content``. Cancelled shoots and the studio's internal notes are never shown.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from apps.audit.services import record
from apps.shoots.models import Shoot
from apps.studio.portal import (
    PortalSection,
    portal_permission_required,
    portal_permissions,
    register_section,
    register_upcoming,
)

KEY = "portal_view_content"


def _visible(workspace):
    return Shoot.objects.filter(workspace=workspace).exclude(status=Shoot.Status.CANCELLED)


@register_section
def _section(request):
    ws = request.portal_workspace
    if (
        portal_permissions(request).get(KEY)
        and _visible(ws).filter(date__gte=timezone.localdate() - timedelta(days=30)).exists()
    ):
        return PortalSection("shoots_portal", gettext_lazy("Shoots"), reverse("shoots_portal:index"), order=35)
    return None


@portal_permission_required(KEY)
@require_GET
def index(request):
    ws = request.portal_workspace
    today = timezone.localdate()
    shoots = list(
        _visible(ws).filter(date__gte=today - timedelta(days=30)).select_related("client_contact").order_by("date")
    )
    return render(
        request,
        "shoots/portal/index.html",
        {
            "workspace": ws,
            "upcoming": [s for s in shoots if s.date >= today],
            "past": [s for s in shoots if s.date < today][::-1],
            "can_tick": portal_permissions(request).get("portal_approve", False),
        },
    )


@portal_permission_required("portal_approve")
@require_POST
def prep(request, shoot_id):
    shoot = get_object_or_404(_visible(request.portal_workspace).filter(date__gte=timezone.localdate()), pk=shoot_id)
    done = set(request.POST.getlist("done"))
    shoot.prep_checklist = [
        {**item, "done": str(i) in done} for i, item in enumerate(shoot.prep_checklist) if isinstance(item, dict)
    ]
    shoot.save(update_fields=["prep_checklist", "updated_at"])
    ticked = sum(1 for i in shoot.prep_checklist if i.get("done"))
    record(
        "shoot.prep_updated",
        f"Client updated the preparation checklist for {shoot.title} ({ticked} of {len(shoot.prep_checklist)} done)",
        workspace=shoot.workspace,
        actor=request.user,
        target=shoot,
    )
    messages.success(request, _("Checklist saved. The studio can see it."))
    return redirect(reverse("shoots_portal:index") + f"#shoot-{shoot.id}")


@register_upcoming
def _coming_up(request):
    from apps.studio.portal import UpcomingItem

    if not portal_permissions(request).get(KEY):
        return []
    today = timezone.localdate()
    return [
        UpcomingItem(
            s.date,
            _("Shoot: %(title)s") % {"title": s.title},
            reverse("shoots_portal:index") + f"#shoot-{s.id}",
            "shoot",
        )
        for s in _visible(request.portal_workspace).filter(date__gte=today, date__lte=today + timedelta(days=21))[:3]
    ]
