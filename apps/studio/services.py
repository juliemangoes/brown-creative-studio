"""Small helpers for putting people into the studio."""

from __future__ import annotations

from apps.members.models import OrgMembership


def drop_default_org(user, keep_organization) -> None:
    """Remove the empty "My Organization" that signup provisioned, once a user joins the studio.

    Mirrors what BrightBean does after an invite signup (``apps.accounts.signals``):
    only the auto-provisioned organization, and only if nobody else is in it.
    """
    from apps.workspaces.models import Workspace

    for membership in (
        OrgMembership.objects.filter(user=user).exclude(organization=keep_organization).select_related("organization")
    ):
        org = membership.organization
        if org.name != "My Organization":
            continue
        membership.delete()
        if not org.memberships.exists():
            Workspace.objects.filter(organization=org).delete()
            org.delete()
