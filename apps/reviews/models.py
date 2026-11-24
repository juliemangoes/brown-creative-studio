"""What exactly was approved, by whom, at which stage; pins on media; each client's approval rule.

BrightBean's ``ApprovalAction`` says who did what. ``ApprovalRecord`` adds which
version: a fingerprint of the reviewable content of each platform post (text,
media, per-platform overrides and settings) and a link to the ``PostVersion``
snapshot. Scheduling checks the fingerprint, so a change after approval can't
publish.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedModel


class ApprovalRecord(WorkspaceScopedModel):
    class Stage(models.TextChoices):
        INTERNAL = "internal", "Studio review"
        CLIENT = "client", "Client approval"
        OVERRIDE = "override", "Client approval overridden"
        SYSTEM = "system", "System"

    class Decision(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REJECTED = "rejected", "Rejected"
        RESUBMITTED = "resubmitted", "Resubmitted"
        HELD = "held", "Hold requested"
        RESUMED = "resumed", "Hold lifted"
        REOPENED = "reopened", "Reopened after a change"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="approval_records")
    platform_post = models.ForeignKey(
        "composer.PlatformPost", on_delete=models.CASCADE, null=True, blank=True, related_name="approval_records"
    )
    action = models.OneToOneField(
        "approvals.ApprovalAction", on_delete=models.SET_NULL, null=True, blank=True, related_name="record"
    )
    version = models.ForeignKey(
        "composer.PostVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="approval_records"
    )
    stage = models.CharField(max_length=20, choices=Stage.choices)
    decision = models.CharField(max_length=20, choices=Decision.choices)
    # {platform_post_id: sha256 of its reviewable content} at the moment of the decision.
    fingerprints = models.JSONField(default=dict)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "reviews_approval_record"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.get_stage_display()}: {self.get_decision_display()}"

    def covers(self, platform_post) -> bool:
        return self.platform_post_id is None or self.platform_post_id == platform_post.id


class CommentPin(models.Model):
    """Where a comment points: a spot on an image (x, y as fractions) or a moment in a video."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.OneToOneField("approvals.PostComment", on_delete=models.CASCADE, related_name="pin")
    media_asset = models.ForeignKey("media_library.MediaAsset", on_delete=models.CASCADE, related_name="comment_pins")
    x = models.FloatField(null=True, blank=True)
    y = models.FloatField(null=True, blank=True)
    timestamp_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "reviews_comment_pin"

    def label(self) -> str:
        if self.timestamp_seconds is not None:
            total = int(self.timestamp_seconds)
            return f"at {total // 60}:{total % 60:02d}"
        if self.x is not None and self.y is not None:
            return f"pinned at {round(self.x * 100)}%, {round(self.y * 100)}%"
        return ""


class ApprovalRule(models.Model):
    """A client's approval rule: who approves, in what order, how long each stage has.

    Order is always studio review first, then the client (when the workspace
    mode includes the client). Empty approver lists mean anyone with the right
    role may approve.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.OneToOneField("workspaces.Workspace", on_delete=models.CASCADE, related_name="approval_rule")
    internal_approvers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    client_approvers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    internal_hours = models.PositiveSmallIntegerField(default=24, help_text="Hours the studio has to review.")
    client_hours = models.PositiveSmallIntegerField(default=48, help_text="Hours the client has to approve.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reviews_approval_rule"
