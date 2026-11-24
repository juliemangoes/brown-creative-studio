"""Content collection days: shoots, their shot lists, and what was captured.

Every shot can be tied to the planned post it serves, so a post's history runs
from idea to shot to approval.
"""

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.managers import WorkspaceScopedModel


def _token():
    return secrets.token_urlsafe(24)


class Shoot(WorkspaceScopedModel):
    class Status(models.TextChoices):
        PLANNED = "planned", _("Planned")
        CONFIRMED = "confirmed", _("Confirmed")
        DONE = "done", _("Done")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=160)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED, db_index=True)
    date = models.DateField(db_index=True)
    call_time = models.TimeField()
    wrap_time = models.TimeField(null=True, blank=True)
    locations = models.JSONField(default=list, blank=True)  # [str]
    crew = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="shoots")
    talent = models.JSONField(default=list, blank=True)  # [{"name", "role", "release": bool}]
    client_contact = models.ForeignKey(
        "clients.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="shoots"
    )
    run_of_show = models.JSONField(default=list, blank=True)  # [{"time", "item"}]
    equipment = models.JSONField(default=list, blank=True)  # [str]
    props = models.TextField(blank=True, default="")
    wardrobe = models.TextField(blank=True, default="")
    permits = models.TextField(blank=True, default="")
    release_forms = models.TextField(
        blank=True, default="", help_text="Which releases are needed and where signed ones are kept."
    )
    prep_checklist = models.JSONField(default=list, blank=True)  # [{"text", "done"}] for the client
    client_notes = models.TextField(blank=True, default="", help_text="Shown on the call sheet.")
    notes = models.TextField(blank=True, default="", help_text="Internal; never on the call sheet.")
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="shoots"
    )
    share_token = models.CharField(max_length=64, unique=True, default=_token)
    share_enabled = models.BooleanField(default=False)
    prep_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shoots_shoot"
        ordering = ["date", "call_time"]

    def __str__(self):
        return f"{self.title} ({self.date})"


class Shot(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", _("To shoot")
        CAPTURED = "captured", _("Captured")
        RETAKE = "retake", _("Retake")
        SKIPPED = "skipped", _("Skipped")

    class Priority(models.TextChoices):
        MUST = "must", _("Must have")
        NICE = "nice", _("Nice to have")

    # Client-generated ids are accepted (on-set mode creates shots offline).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shoot = models.ForeignKey(Shoot, on_delete=models.CASCADE, related_name="shots")
    workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.CASCADE, related_name="shots")
    post = models.ForeignKey("composer.Post", on_delete=models.SET_NULL, null=True, blank=True, related_name="shots")
    code = models.CharField(max_length=12, blank=True, default="", help_text="Short code for matching files, e.g. S03.")
    description = models.CharField(max_length=300)
    format = models.CharField(max_length=10, blank=True, default="")
    location = models.CharField(max_length=160, blank=True, default="")
    priority = models.CharField(max_length=5, choices=Priority.choices, default=Priority.MUST)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLANNED)
    notes = models.TextField(blank=True, default="")
    is_unplanned = models.BooleanField(default=False, help_text="An idea captured on set.")
    position = models.PositiveIntegerField(default=0)
    captured_at = models.DateTimeField(null=True, blank=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    media = models.ManyToManyField("media_library.MediaAsset", blank=True, related_name="shots")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shoots_shot"
        ordering = ["position", "code"]

    def __str__(self):
        return f"{self.code} {self.description}"

    def save(self, *args, **kwargs):
        self.workspace_id = self.shoot.workspace_id
        super().save(*args, **kwargs)
