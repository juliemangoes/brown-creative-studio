"""Audit entries from BrightBean's own records: approvals, publish attempts and permission changes.

BrightBean's approval history (``ApprovalAction``) and publish log
(``PublishLog``) stay the source of truth; the audit log copies each new entry
into one searchable, studio-wide timeline.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.approvals.models import ApprovalAction
from apps.audit.services import record, record_after_commit
from apps.members.models import CustomRole, OrgMembership, WorkspaceMembership
from apps.publisher.models import PublishLog

_ACTION_WORDS = {
    "submitted": "submitted for review",
    "approved": "approved",
    "changes_requested": "requested changes on",
    "rejected": "rejected",
    "resubmitted": "resubmitted",
    "held": "put on hold",
}


def _post_name(post) -> str:
    return post.title or (post.caption[:60] + ("…" if len(post.caption) > 60 else "")) or "Untitled post"


@receiver(post_save, sender=ApprovalAction, dispatch_uid="audit.approval_action")
def approval_action_saved(sender, instance, created, **kwargs):
    if not created:
        return
    post = instance.post
    platform = ""
    if instance.platform_post_id and instance.platform_post.social_account_id:
        platform = f" on {instance.platform_post.social_account.get_platform_display()}"
    who = getattr(instance.user, "name", "") or getattr(instance.user, "email", "") or "Someone"
    record(
        f"approval.{instance.action}",
        f"{who} {_ACTION_WORDS.get(instance.action, instance.action)} “{_post_name(post)}”{platform}.",
        workspace=post.workspace,
        actor=instance.user,
        target=post,
        data={"platform_post": str(instance.platform_post_id or ""), "comment": instance.comment[:500]},
    )


@receiver(post_save, sender=PublishLog, dispatch_uid="audit.publish_log")
def publish_log_saved(sender, instance, created, **kwargs):
    if not created:
        return
    platform_post = instance.platform_post
    post = platform_post.post
    platform = platform_post.social_account.get_platform_display() if platform_post.social_account_id else "a platform"
    failed = bool(instance.error_message)
    record(
        "publish.failed" if failed else "publish.attempted",
        (f"Publishing “{_post_name(post)}” to {platform} failed: {instance.error_message[:200]}")
        if failed
        else f"“{_post_name(post)}” was sent to {platform} (attempt {instance.attempt_number}).",
        workspace=post.workspace,
        actor=None,
        target=post,
        data={
            "platform_post": str(platform_post.pk),
            "attempt": instance.attempt_number,
            "status_code": instance.status_code,
        },
    )


# ── Permission changes ────────────────────────────────────────────────────────


@receiver(pre_save, sender=WorkspaceMembership, dispatch_uid="audit.ws_membership_pre")
def _remember_workspace_role(sender, instance, **kwargs):
    if instance.pk:
        previous = WorkspaceMembership.objects.filter(pk=instance.pk).values("workspace_role", "custom_role_id").first()
        instance._audit_previous = previous


@receiver(post_save, sender=WorkspaceMembership, dispatch_uid="audit.ws_membership")
def workspace_membership_saved(sender, instance, created, **kwargs):
    role = instance.custom_role.name if instance.custom_role_id else instance.get_workspace_role_display()
    if created:
        record(
            "permission.member_added",
            f"{instance.user.email} was given access to {instance.workspace.name} as {role}.",
            workspace=instance.workspace,
            target=instance,
            data={
                "user": str(instance.user_id),
                "role": instance.workspace_role,
                "custom_role": str(instance.custom_role_id or ""),
            },
        )
        return
    previous = getattr(instance, "_audit_previous", None) or {}
    if (
        previous.get("workspace_role") != instance.workspace_role
        or previous.get("custom_role_id") != instance.custom_role_id
    ):
        record(
            "permission.role_changed",
            f"{instance.user.email}'s role on {instance.workspace.name} changed to {role}.",
            workspace=instance.workspace,
            target=instance,
            data={
                "user": str(instance.user_id),
                "from": previous,
                "to": {"workspace_role": instance.workspace_role, "custom_role_id": str(instance.custom_role_id or "")},
            },
        )


@receiver(post_delete, sender=WorkspaceMembership, dispatch_uid="audit.ws_membership_delete")
def workspace_membership_deleted(sender, instance, **kwargs):
    record_after_commit(
        "permission.member_removed",
        f"{instance.user.email} no longer has access to {instance.workspace.name}.",
        organization_id=instance.workspace.organization_id,
        data={"user": str(instance.user_id), "workspace": str(instance.workspace_id)},
    )


@receiver(pre_save, sender=OrgMembership, dispatch_uid="audit.org_membership_pre")
def _remember_org_role(sender, instance, **kwargs):
    if instance.pk:
        instance._audit_previous_role = (
            OrgMembership.objects.filter(pk=instance.pk).values_list("org_role", flat=True).first()
        )


@receiver(post_save, sender=OrgMembership, dispatch_uid="audit.org_membership")
def org_membership_saved(sender, instance, created, **kwargs):
    if created:
        record(
            "permission.org_member_added",
            f"{instance.user.email} joined the studio as {instance.get_org_role_display()}.",
            organization=instance.organization,
            target=instance,
            data={"user": str(instance.user_id), "role": instance.org_role},
        )
    elif getattr(instance, "_audit_previous_role", instance.org_role) != instance.org_role:
        record(
            "permission.org_role_changed",
            f"{instance.user.email}'s studio role changed to {instance.get_org_role_display()}.",
            organization=instance.organization,
            target=instance,
            data={"user": str(instance.user_id), "from": instance._audit_previous_role, "to": instance.org_role},
        )


@receiver(post_delete, sender=OrgMembership, dispatch_uid="audit.org_membership_delete")
def org_membership_deleted(sender, instance, **kwargs):
    record_after_commit(
        "permission.org_member_removed",
        f"{instance.user.email} was removed from the studio.",
        organization_id=instance.organization_id,
        data={"user": str(instance.user_id)},
    )


@receiver(pre_save, sender=CustomRole, dispatch_uid="audit.custom_role_pre")
def _remember_role_permissions(sender, instance, **kwargs):
    if instance.pk:
        instance._audit_previous_permissions = (
            CustomRole.objects.filter(pk=instance.pk).values_list("permissions", flat=True).first()
        )


@receiver(post_save, sender=CustomRole, dispatch_uid="audit.custom_role")
def custom_role_saved(sender, instance, created, **kwargs):
    previous = getattr(instance, "_audit_previous_permissions", None)
    if not created and previous == instance.permissions:
        return
    changed = sorted(
        k
        for k in set(instance.permissions) | set(previous or {})
        if (previous or {}).get(k) != instance.permissions.get(k)
    )
    record(
        "permission.custom_role_created" if created else "permission.custom_role_changed",
        f"Role “{instance.name}” was {'created' if created else 'changed'}"
        + (f": {', '.join(changed)}." if changed and not created else "."),
        organization=instance.organization,
        target=instance,
        data={"changed": changed},
    )
