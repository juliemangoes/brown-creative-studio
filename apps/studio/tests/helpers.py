"""Builders for studio tests: a studio with clients, staff in each role and client users."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.studio.services import drop_default_org
from apps.workspaces.models import Workspace


def make_user(email, org, org_role="member", name=""):
    user = User.objects.create_user(email=email, password="pw-123456789", name=name, tos_accepted_at=timezone.now())
    OrgMembership.objects.create(user=user, organization=org, org_role=org_role)
    drop_default_org(user, org)
    return user


def join(user, workspace, role):
    return WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=role)


def make_studio():
    """Two clients; an owner on both; staff by role on the restaurant; one client user per client."""
    org = Organization.objects.create(name="Brown Creative Studio")
    restaurant = Workspace.objects.create(organization=org, name="Casa Maya Restaurant")
    skincare = Workspace.objects.create(organization=org, name="Luma Skincare")
    owner = make_user("owner@studio.test", org, "owner", "Julian")
    for ws in (restaurant, skincare):
        join(owner, ws, "owner")
    people = {"org": org, "restaurant": restaurant, "skincare": skincare, "owner": owner}
    for role in ("manager", "editor", "contributor", "viewer"):
        people[role] = make_user(f"{role}@studio.test", org)
        join(people[role], restaurant, role)
    people["restaurant_client"] = make_user("ana@casamaya.test", org)
    join(people["restaurant_client"], restaurant, "client")
    people["skincare_client"] = make_user("lia@luma.test", org)
    join(people["skincare_client"], skincare, "client")
    return people


def make_post(workspace, account, *, status="draft", when=None, title="Post", author=None):
    from apps.composer.models import PlatformPost, Post

    when = when if when is not None else timezone.now() + timedelta(days=3)
    post = Post.objects.create(
        workspace=workspace, title=title, caption=f"{title} caption", scheduled_at=when, author=author
    )
    PlatformPost.objects.create(post=post, social_account=account, status=status, scheduled_at=when)
    return post


def make_account(workspace, name="Main", platform="instagram", status="connected"):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=f"{workspace.id.hex[:8]}-{name}",
        account_name=name,
        connection_status=status,
    )
