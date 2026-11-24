"""Publishing records the studio adds on top of BrightBean's publisher.

``ManualPublishTask`` is a post someone has to put up by hand (TikTok while
Direct Post is off, or a TikTok upload that landed in the creator's inbox).
``PublishAlert`` remembers which pre-publish problem the team was already
told about, so the hourly check alerts once per problem, not once an hour.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.managers import WorkspaceScopedModel


class ManualPublishTask(WorkspaceScopedModel):
    class Reason(models.TextChoices):
        DIRECT_POST_OFF = "direct_post_off", "Posted by hand (TikTok Direct Post is off)"
        INBOX = "inbox", "TikTok put the video in the creator's inbox"

    class Status(models.TextChoices):
        OPEN = "open", "Waiting to be posted"
        DONE = "done", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform_post = models.ForeignKey(
        "composer.PlatformPost", on_delete=models.CASCADE, related_name="manual_publish_tasks"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.DIRECT_POST_OFF)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    due_at = models.DateTimeField(help_text="When the post is meant to go out.")
    reminded_at = models.DateTimeField(null=True, blank=True)
    live_url = models.URLField(max_length=1000, blank=True, default="")
    live_post_id = models.CharField(max_length=255, blank=True, default="")
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_at"]
        constraints = [
            # One open task per post per platform: routing twice must never make two.
            models.UniqueConstraint(
                fields=["platform_post"], condition=Q(status="open"), name="publishing_one_open_task"
            ),
        ]

    def __str__(self):
        return f"Post by hand: {self.platform_post_id} ({self.get_status_display()})"


class PublishAlert(WorkspaceScopedModel):
    """A pre-publish problem the team has been told about (one alert per post, problem and time)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform_post = models.ForeignKey("composer.PlatformPost", on_delete=models.CASCADE, related_name="+")
    code = models.CharField(max_length=40)
    # The scheduled time the alert was about: moving the post to another time re-arms it.
    due_at = models.DateTimeField()
    message = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["platform_post", "code", "due_at"], name="publishing_alert_once"),
        ]
