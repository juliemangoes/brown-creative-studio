"""The publishing queue (all clients and one client), manual posting and safe retries.

Seeing the queue needs ``view_plan``. Retrying, marking a failed post live and
reassigning manual posting need ``publish_directly``. A manual post can be
completed by the person it is assigned to or anyone with ``publish_directly``.
Every query is scoped to the workspace in the URL (or to the user's own staff
memberships on the all-clients page).
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.composer.models import PlatformPost
from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.publishing import checks, services
from apps.publishing.models import ManualPublishTask
from apps.studio.decorators import in_local_time, in_studio_time, studio_staff_required
from apps.studio.permissions import workspace_permission
from apps.studio.today import staff_memberships

PUBLISHED_DAYS = 14
FAILED_DAYS = 30

GROUPS = (
    ("manual_action", "Manual action needed"),
    ("failed", "Failed"),
    ("publishing", "Publishing"),
    ("scheduled", "Scheduled"),
    ("published", "Published"),
)


def queue_rows(workspace_ids, now=None) -> dict:
    """The five queue groups for these workspaces."""
    from django.db.models.functions import Coalesce

    now = now or timezone.now()
    base = (
        PlatformPost.objects.filter(
            post__workspace_id__in=workspace_ids, social_account__platform__in=services.STUDIO_PLATFORMS
        )
        .annotate(due=Coalesce("scheduled_at", "post__scheduled_at"))
        .select_related("post__workspace", "post__author", "social_account")
    )
    open_tasks = {
        t.platform_post_id: t
        for t in ManualPublishTask.objects.filter(
            workspace_id__in=workspace_ids, status=ManualPublishTask.Status.OPEN
        ).select_related("assignee")
    }
    groups = {
        "manual_action": list(base.filter(status="manual_action").order_by("due", "created_at")),
        "failed": list(
            base.filter(status="failed", updated_at__gte=now - timedelta(days=FAILED_DAYS)).order_by("-updated_at")
        ),
        "publishing": list(base.filter(status="publishing").order_by("due", "created_at")),
        "scheduled": list(base.filter(status="scheduled").order_by("due", "created_at")[:200]),
        "published": list(
            base.filter(status="published", published_at__gte=now - timedelta(days=PUBLISHED_DAYS)).order_by(
                "-published_at"
            )[:100]
        ),
    }
    for pp in groups["manual_action"]:
        pp.task = open_tasks.get(pp.id)  # type: ignore[attr-defined]
    for pp in groups["failed"]:
        pp.outcome_unknown = bool((pp.platform_extra or {}).get(services.OUTCOME_UNKNOWN_KEY))  # type: ignore[attr-defined]
    for pp in [*groups["published"], *groups["failed"], *groups["publishing"]]:
        pp.live_url = services.live_url(pp)  # type: ignore[attr-defined]
    return groups


def check_rows(workspace_ids, now=None) -> list:
    rows = []
    for pp in checks.upcoming_posts(workspace_ids, now):
        problems = checks.blocking(checks.check(pp, now))
        if problems:
            rows.append((pp, problems))
    return rows


def _context(workspace_ids, *, can_act):
    groups = queue_rows(workspace_ids)
    return {
        "groups": [(key, label, groups[key]) for key, label in GROUPS],
        "counts": {key: len(groups[key]) for key, _ in GROUPS},
        "checks": check_rows(workspace_ids),
        "can_act": can_act,
        "direct_post": services.direct_post_enabled("tiktok"),
        "check_hours": checks.check_hours(),
    }


@studio_staff_required
@in_studio_time
def queue(request):
    memberships = staff_memberships(request.user, request.org)
    visible = [m for m in memberships if m.effective_permissions.get("view_plan", False)]
    ctx = _context([m.workspace_id for m in visible], can_act=False)
    ctx["all_clients"] = True
    return render(request, "publishing/queue.html", ctx)


@require_permission("view_plan")
@in_local_time
def client_queue(request, workspace_id):
    ctx = _context([request.workspace.id], can_act=workspace_permission(request, "publish_directly"))
    ctx["workspace"] = request.workspace
    return render(request, "publishing/queue.html", ctx)


def _task(request, task_id) -> ManualPublishTask:
    return get_object_or_404(
        ManualPublishTask.objects.select_related(
            "platform_post__post__workspace", "platform_post__social_account", "assignee", "completed_by"
        ),
        pk=task_id,
        workspace=request.workspace,
    )


def _can_complete(request, task) -> bool:
    return task.assignee_id == request.user.id or workspace_permission(request, "publish_directly")


@require_permission("view_plan")
@in_local_time
def task_detail(request, workspace_id, task_id):
    task = _task(request, task_id)
    services.close_if_stale(task)
    task.refresh_from_db()
    pp = task.platform_post
    attachments = list(pp.post.media_attachments.select_related("media_asset").order_by("position"))
    staff = (
        WorkspaceMembership.objects.filter(workspace=request.workspace)
        .exclude(workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT)
        .select_related("user")
        .order_by("user__name")
    )
    return render(
        request,
        "publishing/task.html",
        {
            "workspace": request.workspace,
            "task": task,
            "pp": pp,
            "caption": pp.effective_caption,
            "first_comment": pp.effective_first_comment,
            "attachments": [a for a in attachments if a.media_asset.file],
            "can_complete": _can_complete(request, task),
            "can_reassign": workspace_permission(request, "publish_directly"),
            "staff": [m.user for m in staff],
            "late": task.status == ManualPublishTask.Status.OPEN and task.due_at < timezone.now(),
        },
    )


@require_permission("view_plan")
@require_GET
def task_download(request, workspace_id, task_id, attachment_id):
    task = _task(request, task_id)
    attachment = (
        task.platform_post.post.media_attachments.select_related("media_asset").filter(pk=attachment_id).first()
    )
    if attachment is None or not attachment.media_asset.file:
        raise Http404
    asset = attachment.media_asset
    name = asset.filename if getattr(asset, "filename", "") else asset.file.name.rsplit("/", 1)[-1]
    return FileResponse(asset.file.open("rb"), as_attachment=True, filename=name)


@require_permission("view_plan")
@require_POST
def task_complete(request, workspace_id, task_id):
    task = _task(request, task_id)
    if not _can_complete(request, task):
        raise PermissionDenied("Only the assigned person or someone who can publish can mark this posted.")
    try:
        services.complete_manual(task, request.POST.get("live_url", ""), request.user)
    except services.PublishingError as exc:
        messages.error(request, str(exc))
        return redirect("publishing:task", workspace_id=workspace_id, task_id=task.id)
    messages.success(request, "Marked as published. The live link is saved on the post.")
    return redirect("publishing:client", workspace_id=workspace_id)


@require_permission("publish_directly")
@require_POST
def task_reassign(request, workspace_id, task_id):
    task = _task(request, task_id)
    membership = (
        WorkspaceMembership.objects.filter(workspace=request.workspace, user_id=request.POST.get("assignee"))
        .exclude(workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT)
        .first()
    )
    if membership is None:
        messages.error(request, "Choose someone on this client's team.")
    else:
        services.reassign(task, get_user_model().objects.get(pk=membership.user_id), request.user)
        messages.success(request, f"Assigned to {membership.user.name or membership.user.email}.")
    return redirect("publishing:task", workspace_id=workspace_id, task_id=task.id)


def _platform_post(request, platform_post_id):
    return get_object_or_404(
        PlatformPost.objects.select_related("post__workspace", "social_account"),
        pk=platform_post_id,
        post__workspace=request.workspace,
    )


@require_permission("publish_directly")
@require_POST
def retry(request, workspace_id, platform_post_id):
    pp = _platform_post(request, platform_post_id)
    try:
        services.retry(pp, request.user, confirmed_not_live=request.POST.get("confirmed_not_live") == "1")
    except services.PublishingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Back in the queue. It goes out within a minute.")
    return redirect("publishing:client", workspace_id=workspace_id)


@require_permission("publish_directly")
@require_POST
def mark_live(request, workspace_id, platform_post_id):
    pp = _platform_post(request, platform_post_id)
    try:
        services.mark_live(pp, request.POST.get("live_url", ""), request.user)
    except services.PublishingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Marked as published with its live link.")
    return redirect("publishing:client", workspace_id=workspace_id)
