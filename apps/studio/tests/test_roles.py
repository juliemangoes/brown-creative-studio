"""Roles, permission keys and the client scope guard."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import (
    BUILTIN_ROLE_PERMISSIONS,
    PERMISSION_KEYS,
    OrgMembership,
    WorkspaceMembership,
    has_org_permission,
)
from apps.organizations.models import Organization
from apps.studio.models import StaffPermissionGrant
from apps.studio.permissions import (
    CLIENT_ROLE_PRESETS,
    PORTAL_KEYS,
    STUDIO_ORG_KEYS,
    STUDIO_WORKSPACE_KEYS,
    ensure_client_roles,
    has_studio_permission,
    is_studio_staff,
)
from apps.workspaces.models import Workspace


def _user(email):
    return User.objects.create_user(email=email, password="pw-123456789", tos_accepted_at=timezone.now())


@pytest.fixture
def studio(db):
    org = Organization.objects.create(name="Brown Creative Studio")
    restaurant = Workspace.objects.create(organization=org, name="Casa Maya Restaurant")
    skincare = Workspace.objects.create(organization=org, name="Luma Skincare")
    owner = _user("owner@studio.test")
    OrgMembership.objects.create(user=owner, organization=org, org_role="owner")
    for ws in (restaurant, skincare):
        WorkspaceMembership.objects.create(user=owner, workspace=ws, workspace_role="owner")
    # A client invited the BrightBean way: org member + client role on one workspace.
    customer = _user("ana@casamaya.test")
    OrgMembership.objects.create(user=customer, organization=org, org_role="member")
    WorkspaceMembership.objects.create(user=customer, workspace=restaurant, workspace_role="client")
    return {"org": org, "restaurant": restaurant, "skincare": skincare, "owner": owner, "customer": customer}


# ── Keys and role defaults ───────────────────────────────────────────────────


def test_every_builtin_role_knows_every_studio_key():
    for role, perms in BUILTIN_ROLE_PERMISSIONS.items():
        missing = (set(STUDIO_WORKSPACE_KEYS) | set(PORTAL_KEYS)) - set(perms)
        assert not missing, (role, missing)


def test_studio_keys_are_not_agent_api_scopes():
    assert not (set(STUDIO_WORKSPACE_KEYS) | set(PORTAL_KEYS)) & set(PERMISSION_KEYS)


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    [
        ("manager", {"manage_client_profile", "manage_projects", "manage_shoots"}, {"view_billing", "manage_billing"}),
        (
            "editor",
            {"manage_projects", "manage_shoots", "view_client_profile"},
            {"manage_client_profile", "view_billing"},
        ),
        ("contributor", {"view_projects", "view_shoots"}, {"view_client_profile", "view_billing", "view_analytics"}),
        (
            "client",
            {"portal_approve", "portal_view_content"},
            {"portal_view_billing", "portal_manage_users", "view_internal"},
        ),
    ],
)
def test_role_defaults_follow_the_brief(role, allowed, denied):
    perms = BUILTIN_ROLE_PERMISSIONS[role]
    assert all(perms[k] for k in allowed), role
    assert not any(perms.get(k) for k in denied), role


def test_studio_roles_never_hold_portal_keys():
    for role in ("owner", "manager", "editor", "contributor", "viewer"):
        assert not any(BUILTIN_ROLE_PERMISSIONS[role][k] for k in PORTAL_KEYS), role


@pytest.mark.django_db
def test_client_role_presets(studio):
    roles = ensure_client_roles(studio["org"])
    assert set(roles) == set(CLIENT_ROLE_PRESETS)
    billing = roles["Client billing contact"].permissions
    assert billing["portal_view_billing"] and not billing["portal_view_content"] and not billing["approve_posts"]
    admin = roles["Client admin"].permissions
    assert admin["portal_manage_users"] and admin["portal_view_reports"] and admin["portal_view_billing"]
    reviewer = roles["Client reviewer"].permissions
    assert reviewer["portal_approve"] and not reviewer["portal_view_billing"] and not reviewer["portal_manage_users"]
    assert ensure_client_roles(studio["org"])["Client admin"].pk == roles["Client admin"].pk  # idempotent


@pytest.mark.django_db
def test_org_keys_and_individual_grants(studio):
    owner_membership = OrgMembership.objects.get(user=studio["owner"], organization=studio["org"])
    assert all(has_org_permission(owner_membership, k) for k in STUDIO_ORG_KEYS)
    member = _user("team@studio.test")
    membership = OrgMembership.objects.create(user=member, organization=studio["org"], org_role="member")
    assert not has_studio_permission(membership, "view_finance")
    StaffPermissionGrant.objects.create(org_membership=membership, key="view_finance", granted_by=studio["owner"])
    assert has_studio_permission(membership, "view_finance")
    assert not has_studio_permission(membership, "view_audit_log")


@pytest.mark.django_db
def test_who_counts_as_studio_staff(studio):
    assert is_studio_staff(studio["owner"], studio["org"])
    # An org member with only a client role is not staff of this studio, even
    # though BrightBean gave the account a personal "My Organization" of its own.
    assert not is_studio_staff(studio["customer"], studio["org"])
    editor = _user("ed@studio.test")
    OrgMembership.objects.create(user=editor, organization=studio["org"], org_role="member")
    WorkspaceMembership.objects.create(user=editor, workspace=studio["skincare"], workspace_role="editor")
    assert is_studio_staff(editor, studio["org"])


# ── Client scope guard ───────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    [
        "calendar:calendar",
        "composer:compose",
        "composer:create_landing",
        "media_library:index",
        "analytics:index",
        "inbox:feed",
    ],
)
def test_client_cannot_open_studio_pages_of_their_own_workspace(client, studio, name):
    client.force_login(studio["customer"])
    response = client.get(reverse(name, kwargs={"workspace_id": studio["restaurant"].id}))
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["dashboard", "organizations:cross_workspace_calendar", "notifications:list", "design_system:style_guide"]
)
def test_client_is_sent_to_the_portal_from_studio_wide_pages(client, studio, name):
    client.force_login(studio["customer"])
    response = client.get(reverse(name))
    assert response.status_code == 302
    assert response["Location"] == reverse("client_portal:dashboard")


@pytest.mark.django_db
def test_client_signing_in_with_a_password_lands_in_their_portal(client, studio):
    client.force_login(studio["customer"])
    response = client.get(reverse("dashboard"), follow=True)
    assert response.redirect_chain[-1][0] == reverse("client_portal:dashboard")
    assert response.status_code == 200
    assert client.session["portal_workspace_id"] == str(studio["restaurant"].id)
    assert "Luma Skincare" not in response.content.decode()


@pytest.mark.django_db
def test_client_can_still_use_the_portal_and_its_approval_endpoints(client, studio):
    client.force_login(studio["customer"])
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(studio["restaurant"].id)
    session.save()
    assert client.get(reverse("client_portal:dashboard")).status_code == 200
    assert client.get(reverse("client_portal:approval_queue")).status_code == 200
    # The approval endpoints the portal posts to are not blocked by the guard
    # (a missing post is a 404 from the view, not a 403 from the guard).
    url = reverse(
        "approvals:approve",
        kwargs={"workspace_id": studio["restaurant"].id, "post_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert client.post(url).status_code != 403


@pytest.mark.django_db
def test_client_of_one_workspace_cannot_reach_another(client, studio):
    client.force_login(studio["customer"])
    for name in ("calendar:calendar", "media_library:index", "client_portal_admin:client_list"):
        assert client.get(reverse(name, kwargs={"workspace_id": studio["skincare"].id})).status_code == 403
    url = reverse(
        "approvals:approve",
        kwargs={"workspace_id": studio["skincare"].id, "post_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert client.post(url).status_code == 403


@pytest.mark.django_db
def test_staff_are_unaffected(client, studio):
    client.force_login(studio["owner"])
    assert client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).status_code == 200
    assert client.get(reverse("organizations:cross_workspace_calendar")).status_code == 200


@pytest.mark.django_db
def test_new_studio_owner_is_unaffected(client, db):
    newcomer = _user("new@studio.test")  # BrightBean provisions their own organization
    client.force_login(newcomer)
    assert client.get(reverse("notifications:list")).status_code == 200
