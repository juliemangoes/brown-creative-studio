"""Roles and permissions for Brown Creative Studio, on BrightBean's permission system.

BrightBean resolves a workspace permission with
``WorkspaceMembership.effective_permissions`` (a built-in role's dict, or a
custom role's JSON) and an org permission with ``has_org_permission`` (built-in
by org role). This module adds the keys the studio's modules need to those same
structures at startup, so ``apps.members.decorators.require_permission`` and
custom roles work with them unchanged, and no upstream file is edited.

The new keys are deliberately not added to ``apps.members.models.PERMISSION_KEYS``:
that list is also the set of scopes an Agent API key can carry, and the API stays
limited to studio staff.

Role mapping (the brief's table):

    Studio owner          org owner                  everything
    Studio team member    workspace manager/editor   plan, create, schedule, report; no billing unless granted
    Freelancer            workspace contributor      assigned tasks, shoots and files only
    Client reviewer       workspace client           comment on and approve content
    Client admin          client + "Client admin" custom role
    Client billing contact client + "Client billing contact" custom role
"""

from __future__ import annotations

from typing import Any

# ── Workspace (per-client) keys ───────────────────────────────────────────────
STUDIO_WORKSPACE_KEYS: dict[str, str] = {
    "view_client_profile": "See the client record (pipeline, contacts, services, retainer)",
    "manage_client_profile": "Edit the client record, contacts, services and retainer",
    "view_projects": "See projects and tasks",
    "manage_projects": "Create and edit projects and tasks",
    "view_shoots": "See content collection days",
    "manage_shoots": "Plan content collection days and shot lists",
    "view_billing": "See this client's proposals, contracts and invoices",
    "manage_billing": "Create and send invoices and record payments",
    "view_internal": "See internal notes and team comments",
    "manage_brand": "Edit the brand profile, brand files and brand stages",
}
# What a client may do in the portal. Studio roles never hold these.
PORTAL_KEYS: dict[str, str] = {
    "portal_view_content": "See content in review and the approved calendar",
    "portal_approve": "Comment on and approve content",
    "portal_view_reports": "See reports and results",
    "portal_view_billing": "See and pay invoices and download receipts",
    "portal_manage_users": "Manage the company's portal users",
}
ALL_WORKSPACE_KEYS = {**STUDIO_WORKSPACE_KEYS, **PORTAL_KEYS}

_STAFF_ALL = dict.fromkeys(STUDIO_WORKSPACE_KEYS, True)
_NONE = dict.fromkeys(ALL_WORKSPACE_KEYS, False)

ROLE_DEFAULTS: dict[str, dict[str, bool]] = {
    "owner": {**_NONE, **_STAFF_ALL},
    "manager": {
        **_NONE,
        **_STAFF_ALL,
        "view_billing": False,
        "manage_billing": False,
    },
    "editor": {
        **_NONE,
        "view_client_profile": True,
        "view_projects": True,
        "manage_projects": True,
        "view_shoots": True,
        "manage_shoots": True,
        "view_internal": True,
        "manage_brand": True,
    },
    # Freelancers: what they are assigned to (the projects and shoots apps
    # filter to assignments; these keys only open the sections).
    "contributor": {**_NONE, "view_projects": True, "view_shoots": True, "view_internal": True},
    # The built-in client role is the brief's "Client reviewer".
    "client": {**_NONE, "portal_view_content": True, "portal_approve": True},
    "viewer": {**_NONE, "view_client_profile": True, "view_projects": True, "view_shoots": True},
}

# Client roles beyond the reviewer, created per organization as custom roles on
# top of the built-in "client" workspace role. A custom role's JSON replaces the
# built-in dict entirely, so each preset lists BrightBean's keys too.
CLIENT_ROLE_PRESETS: dict[str, dict[str, bool]] = {
    "Client admin": {
        "approve_posts": True,
        "view_analytics": True,
        "portal_view_content": True,
        "portal_approve": True,
        "portal_view_reports": True,
        "portal_view_billing": True,
        "portal_manage_users": True,
    },
    "Client reviewer": {"approve_posts": True, "portal_view_content": True, "portal_approve": True},
    "Client billing contact": {"portal_view_billing": True},
}

# ── Organization (studio-wide) keys ───────────────────────────────────────────
STUDIO_ORG_KEYS: dict[str, str] = {
    "manage_clients": "Add clients and change their pipeline stage",
    "manage_team": "Invite and remove team members and change roles",
    "view_finance": "See studio revenue, outstanding balances and retainer delivery",
    "manage_billing_settings": "Edit invoice details, taxes, currencies and payment settings",
    "manage_templates": "Edit onboarding, project, proposal and contract templates",
    "manage_integrations": "Connect and check integrations",
    "view_audit_log": "Search the audit log",
    "override_approval": "Schedule without the client's approval, with a logged reason",
}
ORG_ROLE_DEFAULTS: dict[str, set[str]] = {
    "owner": set(STUDIO_ORG_KEYS),
    # BrightBean's invariant: an org admin holds every org permission.
    "admin": set(STUDIO_ORG_KEYS),
    "member": set(),
}


def register() -> None:
    """Add the studio keys to BrightBean's permission structures. Idempotent."""
    from apps.members import models as members

    for role, defaults in ROLE_DEFAULTS.items():
        perms = members.BUILTIN_ROLE_PERMISSIONS.setdefault(role, {})
        for key, value in defaults.items():
            perms.setdefault(key, value)

    known = {key for key, _ in members.ORG_PERMISSION_KEYS}
    members.ORG_PERMISSION_KEYS = tuple(members.ORG_PERMISSION_KEYS) + tuple(  # type: ignore[assignment]
        (key, label) for key, label in STUDIO_ORG_KEYS.items() if key not in known
    )
    for role, keys in ORG_ROLE_DEFAULTS.items():
        members.BUILTIN_ORG_PERMISSIONS.setdefault(role, set()).update(keys)  # type: ignore[call-overload,arg-type]


def client_role_permissions(preset: str) -> dict[str, bool]:
    """The full permission JSON for a client role preset: every key, explicitly."""
    from apps.members.models import PERMISSION_KEYS

    base = dict.fromkeys(list(PERMISSION_KEYS) + list(ALL_WORKSPACE_KEYS), False)
    base.update(CLIENT_ROLE_PRESETS[preset])
    return base


def ensure_client_roles(organization) -> dict[str, Any]:
    """Create (or refresh) the client role presets for ``organization``. Returns name -> CustomRole."""
    from apps.members.models import CustomRole

    roles = {}
    for name in CLIENT_ROLE_PRESETS:
        role, _ = CustomRole.objects.update_or_create(
            organization=organization, name=name, defaults={"permissions": client_role_permissions(name)}
        )
        roles[name] = role
    return roles


def is_client_membership(membership) -> bool:
    """A workspace membership that belongs in the portal, not the studio."""
    return membership is not None and membership.workspace_role == "client"


def is_studio_staff(user, organization=None) -> bool:
    """Studio staff: an org owner or admin, or a member with any non-client workspace role.

    Portal invitations make client users organization members too (BrightBean's
    invite flow), so org membership alone does not make someone staff.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    from apps.members.models import OrgMembership, WorkspaceMembership

    org_memberships = OrgMembership.objects.filter(user=user)
    if organization is not None:  # an Organization or its id
        org_memberships = org_memberships.filter(organization_id=getattr(organization, "pk", organization))
    if org_memberships.filter(org_role__in=["owner", "admin"]).exists():
        return True
    org_ids = list(org_memberships.values_list("organization_id", flat=True))
    if not org_ids:
        return False
    return (
        WorkspaceMembership.objects.filter(user=user, workspace__organization_id__in=org_ids)
        .exclude(workspace_role="client")
        .exists()
    )


def has_studio_permission(org_membership, key: str) -> bool:
    """An org-level permission from the member's role, or granted to them individually."""
    from apps.members.models import has_org_permission

    if org_membership is None:
        return False
    if has_org_permission(org_membership, key):
        return True
    from apps.studio.models import StaffPermissionGrant

    return StaffPermissionGrant.objects.filter(org_membership=org_membership, key=key).exists()


def workspace_permission(request, key: str) -> bool:
    membership = getattr(request, "workspace_membership", None)
    return bool(membership and membership.effective_permissions.get(key, False))
