"""Projects and tasks: per client, across all clients, workload, templates, saved filters.

Client pages resolve the workspace through BrightBean's RBAC middleware and check
``view_projects``/``manage_projects``. Every project and task is fetched through
the URL's workspace (another client's id is a 404) and through ``Scope``, which
limits freelancers to the tasks assigned to them.
"""

from __future__ import annotations

import calendar
import contextlib
import json
from collections import OrderedDict
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.projects.forms import NewProjectForm, ProjectForm, TaskForm, TemplateForm, staff_choices
from apps.projects.models import (
    ChecklistItem,
    Project,
    ProjectKind,
    ProjectTemplate,
    SavedFilter,
    Task,
    TaskComment,
    TaskLink,
)
from apps.projects.services import (
    DUE_FILTERS,
    STATUS_FILTERS,
    apply_filters,
    can_edit_task,
    create_from_template,
    ensure_builtin_templates,
    membership_scope,
    retainer_delivery,
    studio_scope,
    workload,
)
from apps.studio.decorators import in_local_time, in_studio_time, require_studio_permission, studio_staff_required

TASK_VIEWS = [("list", "List"), ("board", "Board"), ("timeline", "Timeline"), ("calendar", "Calendar")]
BOARD_COLUMNS = [Task.Status.TODO, Task.Status.IN_PROGRESS, Task.Status.WAITING_CLIENT, Task.Status.DONE]
FILTER_KEYS = ("view", "status", "assignee", "client", "due", "kind", "milestones", "week", "from", "month")


def _client_scope(request):
    return membership_scope([request.workspace_membership])


def _query(params, **changes):
    merged = {k: v for k, v in params.items() if k in FILTER_KEYS and v}
    merged.update({k: v for k, v in changes.items()})
    return urlencode({k: v for k, v in merged.items() if v})


def _task_views(request, tasks, *, scope, base_url, extra=None, show_client=True):
    """Shared context for list, board, timeline and calendar over ``tasks``."""
    params = request.GET
    view = params.get("view", "list") if params.get("view") in dict(TASK_VIEWS) else "list"
    today = timezone.localdate()
    if view == "board" and "status" not in params:
        filtered = apply_filters(tasks, {**params.dict(), "status": "all"}, request.user)
        filtered = filtered.filter(
            ~Q(status=Task.Status.DONE) | Q(completed_at__gte=timezone.now() - timedelta(days=14))
        )
    else:
        filtered = apply_filters(tasks, params, request.user)
    filtered = filtered.select_related("project", "workspace", "assignee").annotate(
        checklist_total=Count("checklist", distinct=True),
        checklist_done=Count("checklist", filter=Q(checklist__is_done=True), distinct=True),
    )
    ctx = {
        "view": view,
        "task_views": [(key, label, f"{base_url}?{_query(params, view=key)}") for key, label in TASK_VIEWS],
        "status_filters": [(k, label) for k, (label, _) in STATUS_FILTERS.items()],
        "due_filters": list(DUE_FILTERS.items()),
        "kinds": ProjectKind.choices,
        "params": params,
        "base_url": base_url,
        "current_query": _query(params),
        "today": today,
        "show_client": show_client,
        "scope": scope,
        "can_manage_any": bool(scope.can_manage),
    }
    if view == "list":
        ctx["tasks"] = list(filtered.order_by("due_on", "project__name", "position")[:500])
    elif view == "board":
        by_status = OrderedDict((s, []) for s in BOARD_COLUMNS)
        for task in filtered.order_by("position", "due_on"):
            by_status.setdefault(task.status, []).append(task)
        ctx["columns"] = [(s, Task.Status(s).label, by_status[s]) for s in BOARD_COLUMNS]
    elif view == "timeline":
        ctx.update(_timeline(filtered, params, today))
    else:
        ctx.update(_month(filtered, params, today))
    if extra:
        ctx.update(extra)
    return ctx


def _timeline(tasks, params, today, weeks=8):
    try:
        start = date.fromisoformat(params.get("from", ""))
    except ValueError:
        start = today - timedelta(days=7)
    start -= timedelta(days=start.weekday())
    days = weeks * 7
    end = start + timedelta(days=days - 1)
    rows: OrderedDict = OrderedDict()
    for task in (
        tasks.filter(Q(due_on__gte=start) | Q(due_on__isnull=True, starts_on__gte=start))
        .filter(Q(starts_on__lte=end) | Q(starts_on__isnull=True, due_on__lte=end))
        .order_by("project__name", "starts_on", "due_on")
    ):
        first = task.starts_on or task.due_on
        last = task.due_on or task.starts_on
        if first is None:
            continue
        col = max((first - start).days, 0) + 1
        span = max((min(last, end) - max(first, start)).days + 1, 1)
        rows.setdefault(task.project, []).append({"task": task, "col": col, "span": span})
    return {
        "timeline_rows": list(rows.items()),
        "timeline_weeks": [start + timedelta(days=7 * i) for i in range(weeks)],
        "timeline_days": days,
        "timeline_today_col": (today - start).days + 1 if start <= today <= end else None,
        "timeline_prev": (start - timedelta(days=28)).isoformat(),
        "timeline_next": (start + timedelta(days=28)).isoformat(),
    }


def _month(tasks, params, today):
    try:
        year, month = (int(x) for x in params.get("month", "").split("-"))
        first = date(year, month, 1)
    except ValueError:
        first = today.replace(day=1)
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(first.year, first.month)
    by_day: dict = {}
    for task in tasks.filter(due_on__gte=weeks[0][0], due_on__lte=weeks[-1][-1]).order_by("due_on", "position"):
        by_day.setdefault(task.due_on, []).append(task)
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return {
        "month_first": first,
        "month_weeks": [[(d, by_day.get(d, [])) for d in week] for week in weeks],
        "month_prev": prev_month.strftime("%Y-%m"),
        "month_next": next_month.strftime("%Y-%m"),
    }


def _saved_filters(request):
    return SavedFilter.objects.filter(organization=request.org, user=request.user)


# ── All clients ──────────────────────────────────────────────────────────────


@studio_staff_required
@in_studio_time
def all_tasks(request):
    scope = studio_scope(request.user, request.org)
    tasks = scope.tasks(request.user)
    ctx = _task_views(request, tasks, scope=scope, base_url=request.path)
    open_projects = (
        scope.projects(request.user)
        .filter(status__in=[Project.Status.PLANNED, Project.Status.ACTIVE, Project.Status.ON_HOLD])
        .select_related("workspace", "owner")
        .annotate(
            total=Count("tasks", distinct=True),
            done=Count("tasks", filter=Q(tasks__status=Task.Status.DONE), distinct=True),
        )
        .order_by("due_on")[:50]
    )
    ctx.update(
        {
            "clients": scope.workspaces,
            "people": _people(scope),
            "saved_filters": _saved_filters(request),
            "open_projects": open_projects,
        }
    )
    return render(request, "projects/all.html", ctx)


def _people(scope):
    from django.contrib.auth import get_user_model

    ids = [ws.id for ws in scope.workspaces]
    return (
        get_user_model()
        .objects.filter(workspace_memberships__workspace_id__in=ids)
        .exclude(workspace_memberships__workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT)
        .distinct()
        .order_by("name", "email")
    )


@studio_staff_required
@in_studio_time
def workload_view(request):
    scope = studio_scope(request.user, request.org)
    if not scope.can_manage:
        raise PermissionDenied("Workload is for people who manage projects.")
    managed = [ws for ws in scope.workspaces if ws.id in scope.can_manage]
    tasks = Task.objects.filter(workspace_id__in=[ws.id for ws in managed])
    people = list(_people(scope.__class__(managed, set(), scope.can_manage)))
    data = workload(tasks, people)
    return render(request, "projects/workload.html", {"workload": data, "today": timezone.localdate()})


@studio_staff_required
@require_POST
def save_filter(request):
    name = request.POST.get("name", "").strip()[:80]
    query = request.POST.get("query", "")[:500]
    if name:
        SavedFilter.objects.create(organization=request.org, user=request.user, name=name, query=query)
        messages.success(request, f"Saved “{name}”.")
    return redirect(_safe_next(request, reverse("projects:all")))


@studio_staff_required
@require_POST
def delete_filter(request, filter_id):
    get_object_or_404(_saved_filters(request), pk=filter_id).delete()
    return redirect(_safe_next(request, reverse("projects:all")))


# ── Templates ────────────────────────────────────────────────────────────────


@studio_staff_required
def template_list(request):
    ensure_builtin_templates(request.org)
    from apps.studio.permissions import has_studio_permission

    return render(
        request,
        "projects/templates.html",
        {
            "templates": ProjectTemplate.objects.filter(organization=request.org),
            "can_edit": has_studio_permission(request.org_membership, "manage_templates"),
        },
    )


@require_studio_permission("manage_templates")
@require_http_methods(["GET", "POST"])
def template_edit(request, template_id=None):
    template = (
        get_object_or_404(ProjectTemplate, pk=template_id, organization=request.org)
        if template_id
        else ProjectTemplate(organization=request.org)
    )
    form = TemplateForm(
        request.POST or None, instance=template, initial={"tasks_json": json.dumps(template.tasks or [])}
    )
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.tasks = form.cleaned_data["tasks_json"]
        template.save()
        messages.success(
            request, f"Template “{template.name}” saved. New projects use it; existing projects are unchanged."
        )
        return redirect("projects:templates")
    tasks = template.tasks or []
    if request.method == "POST":
        with contextlib.suppress(ValueError):
            tasks = json.loads(request.POST.get("tasks_json") or "[]")
    return render(request, "projects/template_edit.html", {"form": form, "template": template, "tasks_json": tasks})


# ── One client ───────────────────────────────────────────────────────────────


@require_permission("view_projects")
@in_local_time
def client_projects(request, workspace_id):
    from apps.clients.services import profile_for
    from apps.clients.views import STAGE_TONES

    ws = request.workspace
    scope = _client_scope(request)
    projects = (
        scope.projects(request.user)
        .select_related("owner")
        .annotate(
            total=Count("tasks", distinct=True),
            done=Count("tasks", filter=Q(tasks__status=Task.Status.DONE), distinct=True),
        )
        .order_by("-status", "due_on")
    )
    can_manage = ws.id in scope.can_manage
    profile = profile_for(ws)
    ctx = {
        "workspace": ws,
        "profile": profile,
        "stage_tone": STAGE_TONES[profile.stage],
        "open_projects": [p for p in projects if p.is_open],
        "closed_projects": [p for p in projects if not p.is_open],
        "can_manage": can_manage,
        "new_form": NewProjectForm(
            workspace=ws, organization=request.org, initial={"starts_on": timezone.localdate(), "owner": request.user}
        )
        if can_manage
        else None,
        "delivery": retainer_delivery(ws),
        "assigned_only": ws.id in scope.assigned_only,
    }
    if can_manage:
        ensure_builtin_templates(request.org)
    return render(request, "projects/client.html", ctx)


@require_permission("manage_projects")
@require_POST
def project_create(request, workspace_id):
    ws = request.workspace
    form = NewProjectForm(request.POST, workspace=ws, organization=request.org)
    if not form.is_valid():
        messages.error(request, "Choose a template, a name and a start date.")
        return redirect("projects:list", workspace_id=ws.id)
    data = form.cleaned_data
    project = create_from_template(
        ws,
        data["template"],
        name=data["name"],
        starts_on=data["starts_on"],
        owner=data["owner"],
        created_by=request.user,
    )
    messages.success(request, f"{project.name} created with {project.tasks.count()} tasks.")
    return redirect("projects:project", workspace_id=ws.id, project_id=project.id)


def _project(request, project_id):
    scope = _client_scope(request)
    project = get_object_or_404(scope.projects(request.user).filter(workspace=request.workspace), pk=project_id)
    return project, scope


@require_permission("view_projects")
@in_local_time
def project_detail(request, workspace_id, project_id):
    project, scope = _project(request, project_id)
    tasks = scope.tasks(request.user).filter(project=project)
    base_url = request.path
    ctx = _task_views(request, tasks, scope=scope, base_url=base_url, show_client=False)
    can_manage = project.workspace_id in scope.can_manage
    total = project.tasks.count()
    done = project.tasks.filter(status=Task.Status.DONE).count()
    ctx.update(
        {
            "workspace": request.workspace,
            "project": project,
            "can_manage": can_manage,
            "project_form": ProjectForm(instance=project, workspace=request.workspace) if can_manage else None,
            "task_form": TaskForm(project=project, initial={"assignee": request.user}) if can_manage else None,
            "progress": {"total": total, "done": done, "percent": round(100 * done / total) if total else 0},
            "milestones": project.tasks.filter(is_milestone=True).order_by("due_on"),
            "people": staff_choices(request.workspace),
        }
    )
    return render(request, "projects/project.html", ctx)


@require_permission("manage_projects")
@require_POST
def project_update(request, workspace_id, project_id):
    project, _ = _project(request, project_id)
    form = ProjectForm(request.POST, instance=project, workspace=request.workspace)
    if form.is_valid():
        project = form.save(commit=False)
        project.completed_at = (
            timezone.now()
            if project.status == Project.Status.DONE and not project.completed_at
            else project.completed_at
        )
        project.save()
        messages.success(request, "Project saved.")
    else:
        messages.error(
            request, "Check the project details: " + "; ".join(e for errs in form.errors.values() for e in errs)
        )
    return redirect("projects:project", workspace_id=workspace_id, project_id=project.id)


@require_permission("manage_projects")
@require_POST
def task_create(request, workspace_id, project_id):
    project, _ = _project(request, project_id)
    form = TaskForm(request.POST, project=project)
    if form.is_valid():
        task = form.save(commit=False)
        task.project, task.workspace, task.created_by = project, project.workspace, request.user
        task.position = (project.tasks.order_by("-position").values_list("position", flat=True).first() or 0) + 1
        task.save()
        form.save_m2m()
        messages.success(request, f"Task “{task.title}” added.")
    else:
        messages.error(request, "Check the task: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect(_safe_next(request, project_url(project)))


def project_url(project):
    return reverse("projects:project", kwargs={"workspace_id": project.workspace_id, "project_id": project.id})


def _task(request, task_id):
    scope = _client_scope(request)
    task = get_object_or_404(
        scope.tasks(request.user).filter(workspace=request.workspace).select_related("project", "assignee"), pk=task_id
    )
    return task, scope


@require_permission("view_projects")
@in_local_time
@require_http_methods(["GET", "POST"])
def task_detail(request, workspace_id, task_id):
    task, scope = _task(request, task_id)
    manage = task.workspace_id in scope.can_manage
    editable = can_edit_task(scope, task, request.user)
    form = TaskForm(request.POST or None, instance=task, project=task.project) if manage else None
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "Task saved.")
            return redirect(_safe_next(request, project_url(task.project)))
        messages.error(request, "Check the task: " + "; ".join(e for errs in form.errors.values() for e in errs))
    return render(
        request,
        "projects/task.html",
        {
            "workspace": request.workspace,
            "task": task,
            "form": form,
            "can_manage": manage,
            "editable": editable,
            "statuses": Task.Status.choices,
            "next": _safe_next(request, project_url(task.project)),
            "blocked_by": task.open_dependencies(),
        },
    )


@require_permission("view_projects")
@require_POST
def task_status(request, workspace_id, task_id):
    task, scope = _task(request, task_id)
    if not can_edit_task(scope, task, request.user):
        raise PermissionDenied
    status = request.POST.get("status", "")
    if status not in Task.Status.values:
        return HttpResponse(status=400)
    task.status = status
    if "position" in request.POST and request.POST["position"].isdigit():
        task.position = int(request.POST["position"])
    task.save(update_fields=["status", "position", "updated_at"])
    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({"status": task.status, "label": task.get_status_display()})
    return redirect(_safe_next(request, project_url(task.project)))


@require_permission("view_projects")
@require_POST
def checklist_add(request, workspace_id, task_id):
    task, scope = _task(request, task_id)
    if not can_edit_task(scope, task, request.user):
        raise PermissionDenied
    text = request.POST.get("text", "").strip()[:300]
    if text:
        ChecklistItem.objects.create(task=task, text=text, position=task.checklist.count())
    return redirect(_back(request, task))


@require_permission("view_projects")
@require_POST
def checklist_toggle(request, workspace_id, task_id, item_id):
    task, scope = _task(request, task_id)
    if not can_edit_task(scope, task, request.user):
        raise PermissionDenied
    item = get_object_or_404(ChecklistItem, pk=item_id, task=task)
    item.is_done = not item.is_done
    item.save(update_fields=["is_done"])
    return redirect(_back(request, task))


@require_permission("view_projects")
@require_POST
def comment_add(request, workspace_id, task_id):
    task, _ = _task(request, task_id)
    body = request.POST.get("body", "").strip()
    if body:
        TaskComment.objects.create(task=task, author=request.user, body=body[:5000])
    return redirect(_back(request, task))


@require_permission("view_projects")
@require_POST
def link_add(request, workspace_id, task_id):
    from django.core.exceptions import ValidationError
    from django.core.validators import URLValidator

    task, scope = _task(request, task_id)
    if not can_edit_task(scope, task, request.user):
        raise PermissionDenied
    url, label = request.POST.get("url", "").strip(), request.POST.get("label", "").strip()[:160]
    try:
        URLValidator(schemes=["http", "https"])(url)
    except ValidationError:
        messages.error(request, "That link isn't a web address (it should start with https://).")
        return redirect(_back(request, task))
    TaskLink.objects.create(task=task, url=url, label=label or url[:160], created_by=request.user)
    return redirect(_back(request, task))


@require_permission("manage_projects")
@require_POST
def task_delete(request, workspace_id, task_id):
    task, _ = _task(request, task_id)
    project = task.project
    title = task.title
    task.delete()
    messages.success(request, f"Task “{title}” deleted.")
    return redirect(project_url(project))


def _back(request, task):
    url = reverse("projects:task", kwargs={"workspace_id": task.workspace_id, "task_id": task.id})
    nxt = _safe_next(request, "")
    return f"{url}?{urlencode({'next': nxt})}" if nxt else url


def _safe_next(request, default):
    """A same-site path from ``next``, else ``default`` (never an open redirect)."""
    from django.utils.http import url_has_allowed_host_and_scheme

    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return default
