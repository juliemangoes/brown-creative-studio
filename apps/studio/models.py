import uuid

from django.conf import settings
from django.db import models

from apps.studio.permissions import STUDIO_ORG_KEYS


class StaffPermissionGrant(models.Model):
    """An org-level permission given to one team member beyond their role ("unless granted")."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_membership = models.ForeignKey(
        "members.OrgMembership", on_delete=models.CASCADE, related_name="studio_permission_grants"
    )
    key = models.CharField(max_length=64, choices=[(k, label) for k, label in STUDIO_ORG_KEYS.items()])
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "studio_staff_permission_grant"
        unique_together = [("org_membership", "key")]

    def __str__(self):
        return f"{self.org_membership.user.email}: {self.key}"
