"""Projects and tasks. BrightBean's idea board is for content ideas; this is project management.

Every project and task belongs to a client workspace (``WorkspaceScopedModel``);
a task's workspace always equals its project's, enforced on save. Templates are
studio-wide (organization).
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.managers import WorkspaceScopedModel


class ProjectKind(models.TextChoices):
    SOCIAL_RETAINER = "social_retainer", "Monthly social retainer"
    SHOOT = "shoot", "Shoot"
    CAMPAIGN = "campaign", "Campaign"
    BRAND = "brand", "Brand project"
    STRATEGY = "strategy", "Brand strategy"
    ONBOARDING = "onboarding", "Onboarding"
    OTHER = "other", "Other"


class ProjectTemplate(models.Model):
    """A reusable task list. ``tasks`` is a list of dicts:

    ``{"key", "title", "offset_days", "duration_days", "milestone", "assign": "owner"|"",
    "checklist": [str], "depends_on": [key], "description"}``
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="project_templates"
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=ProjectKind.choices)
    description = models.TextField(blank=True, default="")
    tasks = models.JSONField(default=list, blank=True)
    builtin_key = models.CharField(
        max_length=40, blank=True, default="", help_text="Set for the studio's starter templates."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects_template"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "builtin_key"],
                condition=~models.Q(builtin_key=""),
                name="projects_template_builtin_unique",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def length_days(self) -> int:
        return max((t.get("offset_days", 0) + t.get("duration_days", 1) for t in self.tasks), default=0)


class Project(WorkspaceScopedModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "In progress"
        ON_HOLD = "on_hold", "On hold"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=20, choices=ProjectKind.choices, default=ProjectKind.OTHER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_projects"
    )
    starts_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    # Internal brief: never shown in the portal.
    description = models.TextField(blank=True, default="")
    # What the client sees in the portal next to milestones and progress.
    client_summary = models.TextField(blank=True, default="")
    show_in_portal = models.BooleanField(default=True)
    template = models.ForeignKey(
        ProjectTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="projects"
    )
    period_start = models.DateField(
        null=True, blank=True, help_text="First day of the month a retainer project covers."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "projects_project"
        ordering = ["due_on", "name"]

    def __str__(self):
        return self.name

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.PLANNED, self.Status.ACTIVE, self.Status.ON_HOLD)


class Task(WorkspaceScopedModel):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING_CLIENT = "waiting_client", "Waiting on client"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tasks"
    )
    starts_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True, db_index=True)
    position = models.PositiveIntegerField(default=0)
    # Milestones are what the client sees in the portal: a title, a date and whether it's done.
    is_milestone = models.BooleanField(default=False)
    depends_on = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="blocks")
    template_key = models.CharField(max_length=40, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "projects_task"
        ordering = ["position", "due_on", "created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # A task always lives in its project's client, whatever the caller passed.
        self.workspace_id = self.project.workspace_id
        if self.status == self.Status.DONE and self.completed_at is None:
            self.completed_at = timezone.now()
        elif self.status != self.Status.DONE:
            self.completed_at = None
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list({*kwargs["update_fields"], "workspace", "completed_at"})
        super().save(*args, **kwargs)

    @property
    def is_done(self) -> bool:
        return self.status == self.Status.DONE

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_on and not self.is_done and self.due_on < timezone.localdate())

    def open_dependencies(self):
        return [t for t in self.depends_on.all() if not t.is_done]


class ChecklistItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist")
    text = models.CharField(max_length=300)
    is_done = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "projects_checklist_item"
        ordering = ["position", "id"]


class TaskComment(models.Model):
    """Team discussion on a task. Internal: never shown in the portal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "projects_task_comment"
        ordering = ["created_at"]


class TaskLink(models.Model):
    """A link from a task to a file: the media library, a brand asset, a shared drive."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="links")
    label = models.CharField(max_length=160)
    url = models.URLField(max_length=1000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "projects_task_link"
        ordering = ["created_at"]


class SavedFilter(models.Model):
    """A person's named task filter (view + query string), studio-wide or for one client."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_task_filters")
    name = models.CharField(max_length=80)
    query = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "projects_saved_filter"
        ordering = ["name"]
