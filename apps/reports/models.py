"""Goals, monthly reports and imported figures for each client.

A report freezes its figures (``data``) when it is approved, so what the client
sees never changes under them. ``internal_notes`` never leave the studio: not
in the portal, the share link or the PDF.
"""

from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedModel


class ClientGoal(WorkspaceScopedModel):
    class Period(models.TextChoices):
        MONTHLY = "monthly", "Every month"
        CUSTOM = "custom", "Between two dates"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    metric = models.CharField(max_length=40)
    platform = models.CharField(max_length=20, default="all", help_text="facebook, instagram, tiktok or all")
    target = models.DecimalField(max_digits=14, decimal_places=2)
    period = models.CharField(max_length=10, choices=Period.choices, default=Period.MONTHLY)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Report(WorkspaceScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "Waiting for approval"
        APPROVED = "approved", "Approved"
        SHARED = "shared", "Shared with the client"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    # The summary the client reads: what happened, why it likely happened, what to do next.
    what_happened = models.TextField(blank=True, default="")
    why = models.TextField(blank=True, default="")
    next_steps = models.TextField(blank=True, default="")
    ai_drafted_at = models.DateTimeField(null=True, blank=True)
    ai_provider = models.CharField(max_length=40, blank=True, default="")
    ai_warnings = models.JSONField(default=list, blank=True)
    # Published posts picked to show (PlatformPost ids); empty = the top ones by the report's goal metric.
    highlights = models.JSONField(default=list, blank=True)
    rank_by = models.CharField(max_length=40, default="engagement")
    internal_notes = models.TextField(
        blank=True, default="", help_text="For the studio only. Never shown to the client."
    )
    # Figures as computed for the period; frozen at approval.
    data = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    shared_at = models.DateTimeField(null=True, blank=True)
    share_token = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["share_token"], condition=~models.Q(share_token=""), name="reports_unique_share_token"
            )
        ]

    def __str__(self):
        return self.title

    @property
    def is_editable(self) -> bool:
        return self.status in (self.Status.DRAFT, self.Status.IN_REVIEW)

    @property
    def client_visible(self) -> bool:
        return self.status == self.Status.SHARED

    def new_share_token(self) -> str:
        self.share_token = secrets.token_urlsafe(32)
        return self.share_token


class ImportedMetric(WorkspaceScopedModel):
    """A daily figure imported from a CSV (TikTok exports, older data an API no longer gives). Always labelled."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    social_account = models.ForeignKey(
        "social_accounts.SocialAccount", on_delete=models.CASCADE, related_name="imported_metrics"
    )
    metric = models.CharField(max_length=40)
    date = models.DateField()
    value = models.FloatField()
    source_name = models.CharField(max_length=255, blank=True, default="", help_text="The file it came from.")
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["social_account", "metric", "date"], name="reports_one_import_per_day")
        ]
