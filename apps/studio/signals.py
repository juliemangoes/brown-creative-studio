from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.audit.services import record, record_after_commit
from apps.studio.models import StaffPermissionGrant


@receiver(post_save, sender=StaffPermissionGrant, dispatch_uid="studio.grant_saved")
def grant_saved(sender, instance, created, **kwargs):
    if created:
        record(
            "permission.grant_added",
            f"{instance.org_membership.user.email} was granted “{instance.get_key_display()}”.",
            organization=instance.org_membership.organization,
            target=instance,
            data={"key": instance.key, "user": str(instance.org_membership.user_id)},
        )


@receiver(post_delete, sender=StaffPermissionGrant, dispatch_uid="studio.grant_deleted")
def grant_deleted(sender, instance, **kwargs):
    record_after_commit(
        "permission.grant_removed",
        f"{instance.org_membership.user.email} no longer has “{instance.get_key_display()}”.",
        organization_id=instance.org_membership.organization_id,
        data={"key": instance.key, "user": str(instance.org_membership.user_id)},
    )
