"""Projects in the client portal: milestones and progress, never internal task detail."""

from __future__ import annotations

from django.db.models import Count, Q
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from apps.projects.models import Project, Task
from apps.studio.portal import PortalSection, portal_permission_required, portal_permissions, register_section


def _visible(workspace):
    return Project.objects.filter(workspace=workspace, show_in_portal=True).exclude(status=Project.Status.CANCELLED)


@register_section
def _section(request):
    if portal_permissions(request).get("portal_view_content") and _visible(request.portal_workspace).exists():
        return PortalSection("projects_portal", _("Projects"), reverse("projects_portal:list"), order=30)
    return None


@portal_permission_required("portal_view_content")
@require_GET
def project_list(request):
    projects = (
        _visible(request.portal_workspace)
        .annotate(total=Count("tasks"), done=Count("tasks", filter=Q(tasks__status=Task.Status.DONE)))
        .order_by("status", "due_on")
    )
    rows = []
    for project in projects:
        # Only milestone titles, dates and whether they're done: no task notes, assignees or comments.
        milestones = list(
            project.tasks.filter(is_milestone=True).order_by("due_on").values("title", "due_on", "status")
        )
        rows.append(
            {
                "project": project,
                "milestones": milestones,
                "percent": round(100 * project.done / project.total) if project.total else 0,
            }
        )
    return render(request, "projects/portal/list.html", {"workspace": request.portal_workspace, "rows": rows})
