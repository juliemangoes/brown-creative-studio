"""Client review in the portal: a whole week or month at once, with pins on images and video moments."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.composer.models import Post
from apps.reviews.services import attach_pin, due_at
from apps.studio.portal import PortalNotice, portal_permission_required, portal_permissions, register_notice


def _pending(workspace):
    return Post.objects.filter(workspace=workspace, platform_posts__status="pending_client").distinct()


@register_notice
def _batch_notice(request):
    match = getattr(request, "resolver_match", None)
    if not match or match.namespace != "client_portal" or match.url_name != "approval_queue":
        return None
    if not portal_permissions(request).get("portal_approve"):
        return None
    count = _pending(request.portal_workspace).count()
    if count < 2:
        return None
    return PortalNotice(
        _("%(n)s posts are waiting. Review them a week or a month at a time.") % {"n": count},
        reverse("reviews_portal:batch"),
        _("Review by week"),
    )


def _window(period, start_param, tz):
    today = timezone.localdate()
    try:
        start = date.fromisoformat(start_param) if start_param else today
    except ValueError:
        start = today
    if period == "month":
        start = start.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        prev, nxt = (start - timedelta(days=1)).replace(day=1), end
    else:
        start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=7)
        prev, nxt = start - timedelta(days=7), end
    lo, hi = (timezone.make_aware(datetime.combine(d, time.min), tz) for d in (start, end))
    return start, end, prev, nxt, lo, hi


@portal_permission_required("portal_view_content")
@require_GET
def batch(request):
    workspace = request.portal_workspace
    tz = timezone.get_current_timezone()
    period = request.GET.get("period", "week") if request.GET.get("period") in ("week", "month", "all") else "week"
    posts = _pending(workspace).prefetch_related("platform_posts__social_account", "media_attachments__media_asset")
    if period != "all":
        start, end, prev, nxt, lo, hi = _window(period, request.GET.get("start"), tz)
        posts = posts.filter(
            Q(scheduled_at__gte=lo, scheduled_at__lt=hi)
            | Q(scheduled_at__isnull=True, proposed_publish_at__gte=lo, proposed_publish_at__lt=hi)
        )
    else:
        start = end = prev = nxt = None
    posts = list(posts.order_by("scheduled_at", "proposed_publish_at", "created_at"))
    from apps.approvals.models import PostComment

    rows = []
    for post in posts:
        pending = [pp for pp in post.platform_posts.all() if pp.status == "pending_client"]
        rows.append(
            {
                "post": post,
                "platforms": pending,
                "due": min((d for d in (due_at(pp) for pp in pending) if d), default=None),
                "media": list(post.media_attachments.all()),
                "comments": PostComment.objects.filter(
                    post=post, visibility=PostComment.Visibility.EXTERNAL, deleted_at__isnull=True
                )
                .select_related("author", "pin__media_asset")
                .order_by("created_at"),
            }
        )
    return render(
        request,
        "reviews/portal/batch.html",
        {
            "workspace": workspace,
            "rows": rows,
            "period": period,
            "start": start,
            "end": (end - timedelta(days=1)) if end else None,
            "prev": prev,
            "next": nxt,
            "can_approve": portal_permissions(request).get("portal_approve", False),
            "total_pending": _pending(workspace).count(),
        },
    )


def _back(request):
    nxt = request.POST.get("next", "")
    return nxt if nxt.startswith("/portal/review/") else reverse("reviews_portal:batch")


@portal_permission_required("portal_approve")
@require_POST
def approve_batch(request):
    from apps.approvals.services import bulk_approve

    workspace = request.portal_workspace
    ids = request.POST.getlist("post_ids")
    allowed = list(_pending(workspace).filter(id__in=ids).values_list("id", flat=True))
    if not allowed:
        messages.error(request, _("Nothing to approve here any more."))
        return redirect(_back(request))
    results = bulk_approve([str(i) for i in allowed], request.user, workspace)
    done = sum(1 for _id, ok, _err in results if ok)
    messages.success(request, _("Approved %(n)s post(s). Thank you!") % {"n": done})
    return redirect(_back(request))


@portal_permission_required("portal_approve")
@require_POST
def decide(request, post_id):
    from apps.approvals import services as approval_services

    workspace = request.portal_workspace
    post = get_object_or_404(_pending(workspace), pk=post_id)
    decision = request.POST.get("decision")
    comment = request.POST.get("comment", "").strip()
    try:
        if decision == "approve":
            approval_services.approve_post(post, request.user, workspace, comment)
            messages.success(request, _("Approved."))
        elif decision == "changes":
            approval_services.request_changes(post, request.user, workspace, comment)
            messages.success(request, _("Thanks, the studio has your notes."))
        else:
            raise Http404
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(_back(request))


@portal_permission_required("portal_view_content")
@require_POST
def comment(request, post_id):
    from apps.approvals.comments import create_comment
    from apps.approvals.models import PostComment

    workspace = request.portal_workspace
    post = get_object_or_404(Post.objects.filter(workspace=workspace), pk=post_id)
    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, _("Write a comment first."))
        return redirect(_back(request))
    created = create_comment(
        post=post, author=request.user, body=body[:5000], visibility=PostComment.Visibility.EXTERNAL
    )
    attach_pin(created, request.POST)
    messages.success(request, _("Comment added."))
    return redirect(_back(request))
