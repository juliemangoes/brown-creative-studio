"""Creating projects from templates, who sees which tasks, filters, workload and retainer delivery."""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record
from apps.members.models import WorkspaceMembership
from apps.projects.defaults import BUILTINS
from apps.projects.models import ChecklistItem, Project, ProjectTemplate, Task

# ── Templates ────────────────────────────────────────────────────────────────


def ensure_builtin_templates(organization) -> dict[str, ProjectTemplate]:
    """The studio's starter templates, created on first use and never overwritten after."""
    templates = {}
    for key, (name, kind, description, tasks) in BUILTINS.items():
        template, _ = ProjectTemplate.objects.get_or_create(
            organization=organization,
            builtin_key=key,
            defaults={"name": name, "kind": kind, "description": description, "tasks": tasks},
        )
        templates[key] = template
    return templates


@transaction.atomic
def create_from_template(
    workspace, template, *, name, starts_on, owner=None, created_by=None, period_start=None, client_summary=""
):
    """A project with the template's tasks, dates counted from ``starts_on``."""
    project = Project.objects.create(
        workspace=workspace,
        name=name,
        kind=template.kind,
        status=Project.Status.ACTIVE,
        owner=owner,
        starts_on=starts_on,
        due_on=starts_on + timedelta(days=max(template.length_days - 1, 0)),
        template=template,
        period_start=period_start,
        created_by=created_by,
        description=template.description,
        client_summary=client_summary,
    )
    by_key = {}
    for position, spec in enumerate(template.tasks):
        start = starts_on + timedelta(days=spec.get("offset_days", 0))
        task = Task.objects.create(
            workspace=workspace,
            project=project,
            title=spec["title"],
            description=spec.get("description", ""),
            starts_on=start,
            due_on=start + timedelta(days=max(spec.get("duration_days", 1) - 1, 0)),
            is_milestone=bool(spec.get("milestone")),
            assignee=owner if spec.get("assign") == "owner" else None,
            position=position,
            template_key=spec.get("key", ""),
            created_by=created_by,
        )
        ChecklistItem.objects.bulk_create(
            ChecklistItem(task=task, text=text, position=i) for i, text in enumerate(spec.get("checklist", []))
        )
        by_key[spec.get("key") or str(position)] = task
    for spec in template.tasks:
        task = by_key.get(spec.get("key"))
        deps = [by_key[k] for k in spec.get("depends_on", []) if k in by_key]
        if task and deps:
            task.depends_on.set(deps)
    record(
        "project.created",
        f"Project {project.name} was created from {template.name}.",
        workspace=workspace,
        actor=created_by,
        target=project,
    )
    return project


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_label(day: date) -> str:
    return f"{calendar.month_name[day.month]} {day.year}"


# ── Who sees what ────────────────────────────────────────────────────────────


@dataclass
class Scope:
    """The client workspaces a person sees tasks in, and where they see only their own tasks."""

    workspaces: list
    assigned_only: set  # workspace ids where only tasks assigned to them are visible
    can_manage: set  # workspace ids where they manage projects

    def tasks(self, user):
        ids = [ws.id for ws in self.workspaces]
        qs = Task.objects.filter(workspace_id__in=ids)
        if self.assigned_only:
            qs = qs.filter(~Q(workspace_id__in=self.assigned_only) | Q(assignee=user))
        return qs

    def projects(self, user):
        ids = [ws.id for ws in self.workspaces]
        qs = Project.objects.filter(workspace_id__in=ids)
        if self.assigned_only:
            qs = qs.filter(~Q(workspace_id__in=self.assigned_only) | Q(tasks__assignee=user)).distinct()
        return qs


def membership_scope(memberships) -> Scope:
    workspaces, assigned_only, can_manage = [], set(), set()
    for m in memberships:
        perms = m.effective_permissions
        if m.workspace_role == WorkspaceMembership.WorkspaceRole.CLIENT or not perms.get("view_projects", False):
            continue
        workspaces.append(m.workspace)
        if perms.get("manage_projects", False):
            can_manage.add(m.workspace_id)
        elif m.workspace_role == WorkspaceMembership.WorkspaceRole.CONTRIBUTOR:
            # Freelancers see only what they're assigned (the brief's role table).
            assigned_only.add(m.workspace_id)
    return Scope(workspaces, assigned_only, can_manage)


def studio_scope(user, organization) -> Scope:
    memberships = (
        WorkspaceMembership.objects.filter(
            user=user, workspace__organization=organization, workspace__is_archived=False
        )
        .select_related("workspace", "custom_role")
        .order_by("workspace__name")
    )
    return membership_scope(memberships)


def can_edit_task(scope: Scope, task, user) -> bool:
    """Managers edit any task in their clients; others only update tasks assigned to them."""
    return task.workspace_id in scope.can_manage or task.assignee_id == user.id


# ── Filters ──────────────────────────────────────────────────────────────────

STATUS_FILTERS = {
    "open": ("Open", ~Q(status=Task.Status.DONE)),
    "todo": ("To do", Q(status=Task.Status.TODO)),
    "in_progress": ("In progress", Q(status=Task.Status.IN_PROGRESS)),
    "waiting_client": ("Waiting on client", Q(status=Task.Status.WAITING_CLIENT)),
    "done": ("Done", Q(status=Task.Status.DONE)),
    "all": ("All", Q()),
}
DUE_FILTERS = {
    "": "Any date",
    "overdue": "Overdue",
    "week": "Due this week",
    "month": "Due this month",
    "none": "No date",
}


def apply_filters(qs, params, user, today=None):
    today = today or timezone.localdate()
    status = params.get("status", "open")
    qs = qs.filter(STATUS_FILTERS.get(status, STATUS_FILTERS["open"])[1])
    assignee = params.get("assignee", "")
    if assignee == "me":
        qs = qs.filter(assignee=user)
    elif assignee == "none":
        qs = qs.filter(assignee__isnull=True)
    elif assignee.isdigit():
        qs = qs.filter(assignee_id=int(assignee))
    client = params.get("client", "")
    if client:
        qs = qs.filter(workspace_id=client) if _is_uuid(client) else qs.none()
    kind = params.get("kind", "")
    if kind:
        qs = qs.filter(project__kind=kind)
    if params.get("milestones") == "1":
        qs = qs.filter(is_milestone=True)
    due = params.get("due", "")
    week_start = today - timedelta(days=today.weekday())
    if due == "overdue":
        qs = qs.filter(due_on__lt=today).exclude(status=Task.Status.DONE)
    elif due == "week":
        qs = qs.filter(due_on__gte=week_start, due_on__lt=week_start + timedelta(days=7))
    elif due == "month":
        qs = qs.filter(due_on__year=today.year, due_on__month=today.month)
    elif due == "none":
        qs = qs.filter(due_on__isnull=True)
    week = params.get("week", "")
    if week:
        try:
            start = date.fromisoformat(week)
            qs = qs.filter(due_on__gte=start, due_on__lt=start + timedelta(days=7))
        except ValueError:
            qs = qs.none()
    return qs


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


# ── Workload ─────────────────────────────────────────────────────────────────


def workload(tasks, people, weeks=6, today=None):
    """Open tasks per person: overdue, each of the next ``weeks`` weeks (by due date), and undated."""
    today = today or timezone.localdate()
    start = today - timedelta(days=today.weekday())
    columns = [start + timedelta(days=7 * i) for i in range(weeks)]
    grid: dict = defaultdict(lambda: {"overdue": 0, "weeks": [0] * weeks, "undated": 0, "total": 0})
    for task in tasks.exclude(status=Task.Status.DONE).only("assignee_id", "due_on"):
        row = grid[task.assignee_id]
        if task.due_on is None:
            row["undated"] += 1
        elif task.due_on < today:
            row["overdue"] += 1
        else:
            index = (task.due_on - start).days // 7
            if index >= weeks:
                continue
            row["weeks"][index] += 1
        row["total"] += 1
    empty = {"overdue": 0, "weeks": [0] * weeks, "undated": 0, "total": 0}
    rows = [{"person": p, **grid.get(p.id, empty)} for p in people]
    if None in grid:
        rows.append({"person": None, **grid[None]})
    for row in rows:
        row["cells"] = [{"n": n, "start": columns[i]} for i, n in enumerate(row["weeks"])]
    peak = max((n for r in rows for n in r["weeks"]), default=0)
    return {"columns": columns, "rows": rows, "peak": peak}


# ── Retainer delivery ────────────────────────────────────────────────────────


def retainer_delivery(workspace, months=3, today=None):
    """Planned vs delivered posts for the current and previous months.

    Posts count once however many platforms they went to. Reels, stories and
    shoot days are shown as planned only: the content and shoot records that
    count them arrive with content operations.
    """
    from apps.clients.models import Retainer
    from apps.composer.models import PlatformPost

    today = today or timezone.localdate()
    retainer = Retainer.current_for(workspace)
    rows = []
    start = month_start(today)
    for _ in range(months):
        end = (start + timedelta(days=32)).replace(day=1)
        lo = timezone.make_aware(datetime.combine(start, time.min))
        hi = timezone.make_aware(datetime.combine(end, time.min))
        published = (
            PlatformPost.objects.filter(
                post__workspace=workspace, status="published", published_at__gte=lo, published_at__lt=hi
            )
            .values("post_id")
            .distinct()
            .count()
        )
        scheduled = (
            PlatformPost.objects.filter(
                post__workspace=workspace, scheduled_at__gte=max(lo, timezone.now()), scheduled_at__lt=hi
            )
            .exclude(status__in=["published", "failed", "rejected"])
            .values("post_id")
            .distinct()
            .count()
        )
        planned = retainer.posts_per_month if retainer else None
        rows.append(
            {
                "month": start,
                "label": month_label(start),
                "planned": planned,
                "published": published,
                "scheduled": scheduled,
                "percent": min(100, round(100 * published / planned)) if planned else None,
                "is_current": start == month_start(today),
            }
        )
        start = (start - timedelta(days=1)).replace(day=1)
    return {"retainer": retainer, "months": rows}
