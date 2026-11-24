"""Messages between a client and the studio, each conversation tied to one item so feedback is not lost.

Everything here is visible to the client: there are no internal messages (team notes
belong in internal comments on posts and tasks).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.managers import WorkspaceScopedModel


class Conversation(WorkspaceScopedModel):
    class About(models.TextChoices):
        GENERAL = "general", "General"
        POST = "post", "Post"
        INVOICE = "invoice", "Invoice"
        PROPOSAL = "proposal", "Proposal"
        CONTRACT = "contract", "Contract"
        SHOOT = "shoot", "Shoot"
        BRAND_STAGE = "brand_stage", "Brand stage"
        PROJECT = "project", "Project"

    # Topics only people who see billing may read (on either side).
    BILLING_TOPICS = (About.INVOICE, About.PROPOSAL, About.CONTRACT)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.CharField(max_length=200)
    about = models.CharField(max_length=12, choices=About.choices, default=About.GENERAL)
    about_id = models.UUIDField(null=True, blank=True)
    about_label = models.CharField(max_length=200, blank=True, default="")
    is_closed = models.BooleanField(default=False)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return self.subject

    @property
    def is_billing(self) -> bool:
        return self.about in self.BILLING_TOPICS


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    from_client = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]


class ReadMark(models.Model):
    """When a person last read a conversation (for unread counts)."""

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="conversations_read_once")]
