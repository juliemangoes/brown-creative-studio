"""The content calendar in the client portal: what is approved, scheduled and recently published.

Needs ``portal_view_content``. Drafts and posts still in studio review are not shown
(those reach the client through Approvals), and nothing internal is.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, time, timedelta

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET

from apps.studio.portal import (
    PortalSection,
    portal_permission_required,
    portal_permissions,
    register_section,
    register_upcoming,
)

KEY = "portal_view_content"
SHOWN = ("approved", "scheduled", "publishing", "published")


@register_section
def _section(request):
    if portal_permissions(request).get(KEY):
        return PortalSection("planning_portal", gettext_lazy("Calendar"), reverse("planning_portal:calendar"), order=15)
    return None


def _start(request):
    from datetime import date

    try:
        return date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        return timezone.localdate() - timedelta(days=timezone.localdate().weekday())


@portal_permission_required(KEY)
@require_GET
def calendar(request):
    from apps.composer.models import PlatformPost

    ws = request.portal_workspace
    first = _start(request)
    last = first + timedelta(days=28)
    tz = timezone.get_current_timezone()
    lo, hi = (timezone.make_aware(datetime.combine(d, time.min), tz) for d in (first, last))
    rows = (
        PlatformPost.objects.filter(post__workspace=ws, status__in=SHOWN)
        .filter(scheduled_at__gte=lo, scheduled_at__lt=hi)
        .select_related("post", "social_account")
        .prefetch_related("post__media_attachments__media_asset")
        .order_by("scheduled_at", "created_at")
    )
    days: OrderedDict = OrderedDict()
    for pp in rows:
        day = timezone.localtime(pp.scheduled_at).date()
        entry = days.setdefault(day, OrderedDict()).setdefault(
            pp.post_id,
            {"post": pp.post, "time": timezone.localtime(pp.scheduled_at), "platforms": [], "status": pp.status},
        )
        entry["platforms"].append(pp.social_account.get_platform_display())
        if pp.status == "published":
            entry["status"] = "published"
    return render(
        request,
        "planning/portal/calendar.html",
        {
            "workspace": ws,
            "days": [(d, list(posts.values())) for d, posts in days.items()],
            "first": first,
            "last": last - timedelta(days=1),
            "prev": (first - timedelta(days=28)).isoformat(),
            "next": last.isoformat(),
            "today": timezone.localdate(),
        },
    )


@register_upcoming
def _coming_up(request):
    from apps.composer.models import PlatformPost
    from apps.studio.portal import UpcomingItem

    if not portal_permissions(request).get(KEY):
        return []
    now = timezone.now()
    seen, items = set(), []
    for pp in (
        PlatformPost.objects.filter(
            post__workspace=request.portal_workspace, status="scheduled", scheduled_at__gte=now,
            scheduled_at__lt=now + timedelta(days=7),
        )
        .select_related("post")
        .order_by("scheduled_at")
    ):  # fmt: skip
        if pp.post_id in seen:
            continue
        seen.add(pp.post_id)
        items.append(
            UpcomingItem(
                timezone.localtime(pp.scheduled_at),
                pp.post.title or (pp.post.caption or "")[:60],
                reverse("planning_portal:calendar"),
                "post",
            )
        )
    return items[:4]
