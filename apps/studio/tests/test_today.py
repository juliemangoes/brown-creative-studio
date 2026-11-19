"""Today: real items, sorted and linked, and never another client's data."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.clients.models import ClientProfile, Retainer, ServiceLine
from apps.clients.services import profile_for
from apps.organizations.models import Organization
from apps.studio.tests.helpers import join, make_account, make_post, make_studio, make_user
from apps.studio.today import build_today
from apps.workspaces.models import Workspace


@pytest.fixture
def studio(db):
    people = make_studio()
    now = timezone.now()
    r, s = people["restaurant"], people["skincare"]
    people["r_account"] = make_account(r, "Casa Maya")
    people["s_account"] = make_account(s, "Luma")
    make_post(r, people["r_account"], status="pending_client", when=now + timedelta(days=2), title="Brunch carousel")
    make_post(r, people["r_account"], status="pending_review", when=now - timedelta(hours=5), title="Late taco reel")
    make_post(s, people["s_account"], status="pending_client", when=now + timedelta(days=1), title="Serum launch")
    make_post(r, people["r_account"], status="scheduled", when=now + timedelta(days=3), title="Friday special")
    failed = make_post(s, people["s_account"], status="failed", when=now - timedelta(hours=1), title="Sunscreen tips")
    failed.platform_posts.update(publish_error="Instagram rejected the image: aspect ratio")
    make_account(s, "Luma Page", platform="facebook", status="disconnected")
    return people


def _titles(items):
    return [item.post.title for item in items]


def test_approvals_are_sorted_by_due_date_with_overdue_first(studio):
    today = build_today(studio["owner"], studio["org"])
    assert _titles(today.approvals) == ["Late taco reel", "Serum launch", "Brunch carousel"]
    late = today.approvals[0]
    assert late.overdue and late.waiting_on == "Internal review"
    assert today.approvals[1].due_soon and today.approvals[1].waiting_on == "Client"


def test_next_seven_days_failed_posts_and_accounts(studio):
    today = build_today(studio["owner"], studio["org"])
    upcoming = [item.post.title for items in today.upcoming.values() for item in items]
    assert "Friday special" in upcoming and "Sunscreen tips" not in upcoming
    assert _titles(today.failed) == ["Sunscreen tips"]
    assert "aspect ratio" in today.failed[0].error
    assert [row["account"].account_name for row in today.accounts] == ["Luma Page"]


def test_content_gaps_follow_the_retainer_and_skip_inactive_clients(studio):
    now = timezone.now()
    Retainer.objects.create(
        workspace=studio["restaurant"],
        name="Small",
        monthly_fee=Decimal("500"),
        starts_on=timezone.localdate(),
        posts_per_month=3,
    )
    for day in (5, 12):
        make_post(studio["restaurant"], studio["r_account"], status="approved", when=now + timedelta(days=day))
    ServiceLine.objects.create(workspace=studio["skincare"], kind="social_media", status="paused")
    gaps = {row["workspace"].name: row["signal"] for row in build_today(studio["owner"], studio["org"]).gaps}
    # Restaurant: 3 (pending + scheduled) + 2 approved planned ≥ 3 → no gap. Skincare: social media paused → skipped.
    assert gaps == {}

    profile = profile_for(studio["restaurant"])
    Retainer.objects.filter(workspace=studio["restaurant"]).update(posts_per_month=20)
    gaps = {row["workspace"].name: row["signal"] for row in build_today(studio["owner"], studio["org"]).gaps}
    assert gaps["Casa Maya Restaurant"].tone == "danger"
    assert profile.stage == "active"


def test_stalled_onboarding(studio):
    profile = profile_for(studio["skincare"])
    profile.stage = ClientProfile.Stage.ONBOARDING
    profile.stage_changed_at = timezone.now() - timedelta(days=20)
    profile.save()
    stalled = build_today(studio["owner"], studio["org"]).stalled
    assert [(row["workspace"].name, row["days"]) for row in stalled] == [("Luma Skincare", 20)]
    # Recent onboarding is not stalled.
    profile.stage_changed_at = timezone.now() - timedelta(days=3)
    profile.save()
    assert build_today(studio["owner"], studio["org"]).stalled == []


def test_one_client_view(studio):
    today = build_today(studio["owner"], studio["org"], selected=studio["skincare"])
    assert _titles(today.approvals) == ["Serum launch"]
    assert _titles(today.failed) == ["Sunscreen tips"]


def test_staff_only_see_clients_they_work_on(studio):
    today = build_today(studio["editor"], studio["org"])  # restaurant only
    assert {item.workspace.name for item in today.approvals} == {"Casa Maya Restaurant"}
    assert today.failed == [] and today.accounts == []


def test_selected_workspace_outside_the_users_scope_yields_nothing(studio):
    today = build_today(studio["editor"], studio["org"], selected=studio["skincare"])
    assert today.approvals == [] and today.failed == [] and today.accounts == []


def test_client_memberships_never_feed_today(studio):
    # A studio member who is also a client somewhere: their client workspace stays out.
    join(studio["editor"], studio["skincare"], "client")
    today = build_today(studio["editor"], studio["org"])
    assert {ws.name for ws in today.workspaces} == {"Casa Maya Restaurant"}


# ── Views ────────────────────────────────────────────────────────────────────


def test_today_page_renders_real_items_with_links(client, studio):
    client.force_login(studio["owner"])
    response = client.get(reverse("studio:today"))
    assert response.status_code == 200
    body = response.content.decode()
    for title in ("Late taco reel", "Serum launch", "Friday special", "Sunscreen tips", "Luma Page"):
        assert title in body
    assert reverse("social_accounts:list", kwargs={"workspace_id": studio["skincare"].id}) in body
    assert "?mode=list&amp;tab=approvals" in body


def test_dashboard_sends_staff_to_today(client, studio):
    client.force_login(studio["owner"])
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302 and response["Location"] == reverse("studio:today")


def test_today_for_one_client(client, studio):
    client.force_login(studio["owner"])
    body = client.get(reverse("studio:today_client", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    assert "Brunch carousel" in body and "Serum launch" not in body


def test_today_for_a_client_i_do_not_work_on_is_forbidden(client, studio):
    client.force_login(studio["editor"])
    url = reverse("studio:today_client", kwargs={"workspace_id": studio["skincare"].id})
    assert client.get(url).status_code == 403


def test_client_users_cannot_open_today(client, studio):
    client.force_login(studio["restaurant_client"])
    response = client.get(reverse("studio:today"))
    assert response.status_code == 302 and response["Location"] == reverse("client_portal:dashboard")
    url = reverse("studio:today_client", kwargs={"workspace_id": studio["restaurant"].id})
    assert client.get(url).status_code == 403
    url = reverse("studio:today_client", kwargs={"workspace_id": studio["skincare"].id})
    assert client.get(url).status_code == 403


def test_another_studio_sees_none_of_our_items(client, studio):
    other = Organization.objects.create(name="Another Studio")
    rival = make_user("rival@other.test", other, "owner")
    join(rival, Workspace.objects.create(organization=other, name="Their Client"), "owner")
    client.force_login(rival)
    body = client.get(reverse("studio:today")).content.decode()
    assert "Serum launch" not in body and "Casa Maya" not in body
    assert (
        client.get(reverse("studio:today_client", kwargs={"workspace_id": studio["restaurant"].id})).status_code == 403
    )


def test_today_empty_state_for_a_new_studio(client, db):
    org = Organization.objects.create(name="Fresh Studio")
    owner = make_user("fresh@studio.test", org, "owner")
    client.force_login(owner)
    response = client.get(reverse("studio:today"))
    assert response.status_code == 200
    assert "No clients yet" in response.content.decode()
