"""The audit log: approvals, publish attempts and permission changes are recorded, append-only."""

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.composer.models import PlatformPost, Post
from apps.members.models import CustomRole, OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.publisher.models import PublishLog
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture
def setup(db):
    org = Organization.objects.create(name="Studio")
    ws = Workspace.objects.create(organization=org, name="Casa Maya")
    owner = User.objects.create_user(
        email="owner@studio.test", password="pw-123456789", name="Julian", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=owner, organization=org, org_role="owner")
    account = SocialAccount.objects.create(
        workspace=ws,
        platform="instagram",
        account_platform_id="ig",
        account_name="Casa Maya",
        connection_status="connected",
    )
    post = Post.objects.create(workspace=ws, author=owner, title="Brunch launch", caption="Brunch is back.")
    pp = PlatformPost.objects.create(post=post, social_account=account, status="pending_review")
    return {"org": org, "ws": ws, "owner": owner, "post": post, "pp": pp}


def test_approval_actions_are_recorded(setup):
    ApprovalAction.objects.create(post=setup["post"], platform_post=setup["pp"], user=setup["owner"], action="approved")
    event = AuditEvent.objects.get(verb="approval.approved")
    assert event.workspace == setup["ws"] and event.organization == setup["org"]
    assert event.actor == setup["owner"]
    assert "Julian approved “Brunch launch” on Instagram" in event.summary


def test_publish_attempts_are_recorded(setup):
    PublishLog.objects.create(platform_post=setup["pp"], attempt_number=1, error_message="Rate limited")
    PublishLog.objects.create(platform_post=setup["pp"], attempt_number=2)
    verbs = list(
        AuditEvent.objects.filter(verb__startswith="publish.").order_by("created_at").values_list("verb", flat=True)
    )
    assert verbs == ["publish.failed", "publish.attempted"]


def test_permission_changes_are_recorded(setup):
    editor = User.objects.create_user(email="ed@studio.test", password="pw-123456789", tos_accepted_at=timezone.now())
    membership = WorkspaceMembership.objects.create(user=editor, workspace=setup["ws"], workspace_role="editor")
    membership.workspace_role = "manager"
    membership.save()
    role = CustomRole.objects.create(
        organization=setup["org"], name="Client admin", permissions={"portal_approve": True}
    )
    role.permissions = {"portal_approve": True, "portal_view_billing": True}
    role.save()
    verbs = set(AuditEvent.objects.values_list("verb", flat=True))
    assert {
        "permission.member_added",
        "permission.role_changed",
        "permission.custom_role_created",
        "permission.custom_role_changed",
    } <= verbs
    changed = AuditEvent.objects.get(verb="permission.custom_role_changed")
    assert changed.data["changed"] == ["portal_view_billing"]


def test_removals_are_recorded_after_commit(setup, django_capture_on_commit_callbacks):
    editor = User.objects.create_user(email="ed@studio.test", password="pw-123456789", tos_accepted_at=timezone.now())
    membership = WorkspaceMembership.objects.create(user=editor, workspace=setup["ws"], workspace_role="editor")
    with django_capture_on_commit_callbacks(execute=True):
        membership.delete()
    assert AuditEvent.objects.filter(verb="permission.member_removed", organization=setup["org"]).exists()


def test_deleting_the_studio_is_not_blocked_by_its_audit_trail(setup, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        setup["org"].delete()
    assert not Organization.objects.filter(pk=setup["org"].pk).exists()


def test_events_are_append_only(setup):
    event = record("client.stage_changed", "Casa Maya moved to Active.", workspace=setup["ws"])
    event.summary = "edited"
    with pytest.raises(ValueError):
        event.save()


def test_record_never_breaks_the_caller(setup):
    assert record("x" * 200, "verb too long for the column", organization=setup["org"]) is None
    # The surrounding transaction is still usable.
    assert Organization.objects.filter(pk=setup["org"].pk).exists()
