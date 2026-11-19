import uuid

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One thing that happened, for the studio's audit log. Never edited after it is written."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="audit_events", null=True, blank=True
    )
    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.SET_NULL, related_name="audit_events", null=True, blank=True
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="audit_events", null=True, blank=True
    )
    # Kept as text so the entry still says who it was after the user is deleted.
    actor_label = models.CharField(max_length=255, blank=True, default="")
    verb = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    summary = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="idx_audit_org_time"),
            models.Index(fields=["workspace", "-created_at"], name="idx_audit_ws_time"),
            models.Index(fields=["target_type", "target_id"], name="idx_audit_target"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.verb}: {self.summary}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Audit events are append-only.")
        super().save(*args, **kwargs)
