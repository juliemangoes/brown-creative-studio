"""Creating clients and changing their pipeline stage, with audit entries."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.clients.models import ClientProfile, Contact, ServiceLine
from apps.members.models import WorkspaceMembership
from apps.studio.permissions import ensure_client_roles
from apps.workspaces.models import Workspace


@transaction.atomic
def create_client(
    organization,
    creator,
    *,
    name,
    stage=ClientProfile.Stage.LEAD,
    services=(),
    timezone_name="",
    contact_name="",
    contact_email="",
    industry="",
):
    """A new client: its workspace (with the studio's approval rule), profile, services and first contact."""
    workspace = Workspace.objects.create(
        organization=organization,
        name=name,
        timezone=timezone_name or settings.STUDIO_TIME_ZONE,
        # The brief: internal review, then one client approval, before anything is scheduled.
        approval_workflow_mode=Workspace.ApprovalWorkflowMode.REQUIRED_INTERNAL_AND_CLIENT,
    )
    WorkspaceMembership.objects.create(
        user=creator, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )
    profile = ClientProfile.objects.create(
        workspace=workspace,
        stage=stage,
        industry=industry,
        currency=settings.BILLING_CURRENCIES[0] if settings.BILLING_CURRENCIES else "BZD",
        started_on=timezone.localdate() if stage == ClientProfile.Stage.ACTIVE else None,
    )
    for kind in services:
        ServiceLine.objects.create(
            workspace=workspace,
            kind=kind,
            status=ServiceLine.Status.ACTIVE
            if stage in (ClientProfile.Stage.ACTIVE, ClientProfile.Stage.ONBOARDING)
            else ServiceLine.Status.PROPOSED,
        )
    if contact_name:
        Contact.objects.create(
            workspace=workspace, name=contact_name, email=contact_email, is_primary=True, is_approver=True
        )
    ensure_client_roles(organization)
    record(
        "client.created",
        f"{name} was added as a {profile.get_stage_display().lower()}.",
        workspace=workspace,
        actor=creator,
        target=profile,
    )
    return workspace


def change_stage(profile: ClientProfile, stage: str, actor) -> bool:
    """Move a client to another pipeline stage. Returns False when nothing changed."""
    if stage == profile.stage or stage not in ClientProfile.Stage.values:
        return False
    previous = profile.get_stage_display()
    profile.stage = stage
    profile.stage_changed_at = timezone.now()
    if stage == ClientProfile.Stage.ACTIVE and profile.started_on is None:
        profile.started_on = timezone.localdate()
    profile.save(update_fields=["stage", "stage_changed_at", "started_on", "updated_at"])
    record(
        "client.stage_changed",
        f"{profile.workspace.name} moved from {previous} to {profile.get_stage_display()}.",
        workspace=profile.workspace,
        actor=actor,
        target=profile,
        data={"from": previous, "to": profile.get_stage_display()},
    )
    return True


def profile_for(workspace) -> ClientProfile:
    """The workspace's client profile, created on first use for workspaces made before this app."""
    profile, _ = ClientProfile.objects.get_or_create(
        workspace=workspace,
        defaults={
            "stage": ClientProfile.Stage.ACTIVE,
            "currency": settings.BILLING_CURRENCIES[0] if settings.BILLING_CURRENCIES else "BZD",
        },
    )
    return profile
