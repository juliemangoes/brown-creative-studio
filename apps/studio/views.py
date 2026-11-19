"""Today: the studio's start page."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from apps.studio.decorators import in_studio_time, studio_staff_required
from apps.studio.permissions import is_client_membership
from apps.studio.today import build_today, stall_days


@studio_staff_required
@in_studio_time
def today(request, workspace_id=None):
    selected = None
    if workspace_id is not None:
        # BrightBean's RBAC middleware already refused non-members; clients never get a studio Today.
        if is_client_membership(request.workspace_membership):
            raise PermissionDenied
        selected = request.workspace
    data = build_today(request.user, request.org, selected)
    return render(
        request,
        "studio/today.html",
        {
            "today": data,
            "now": timezone.now(),
            "today_date": timezone.localdate(),
            "stall_days": stall_days(),
            "studio_tz": settings.STUDIO_TIME_ZONE,
        },
    )
