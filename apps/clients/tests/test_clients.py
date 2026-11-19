"""Client list, client pages, contacts, services and retainer, with cross-client access tests."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.clients.health import client_health, runway
from apps.clients.models import ClientProfile, Contact, Retainer, ServiceLine
from apps.clients.services import change_stage, create_client, profile_for
from apps.members.models import WorkspaceMembership
from apps.studio.tests.helpers import join, make_account, make_post, make_studio, make_user
from apps.workspaces.models import Workspace


@pytest.fixture
def studio(db):
    people = make_studio()
    people["restaurant_contact"] = Contact.objects.create(
        workspace=people["restaurant"], name="Ana Pérez", email="ana@casamaya.test"
    )
    people["skincare_contact"] = Contact.objects.create(
        workspace=people["skincare"], name="Lia Chen", email="lia@luma.test"
    )
    return people


def _listed(response):
    return {row["workspace"].name for row in response.context["rows"]}


def _ws_url(name, workspace, **kwargs):
    return reverse(name, kwargs={"workspace_id": workspace.id, **kwargs})


# ── Creating clients and moving them through the pipeline ────────────────────


def test_create_client_sets_up_workspace_profile_services_and_contact(studio):
    ws = create_client(
        studio["org"],
        studio["owner"],
        name="Vista Realty",
        stage=ClientProfile.Stage.ONBOARDING,
        services=["social_media", "branding"],
        contact_name="Marco Ruiz",
        contact_email="marco@vista.test",
    )
    assert ws.approval_workflow_mode == Workspace.ApprovalWorkflowMode.REQUIRED_INTERNAL_AND_CLIENT
    assert ws.timezone == "America/Guatemala"
    assert WorkspaceMembership.objects.get(workspace=ws, user=studio["owner"]).workspace_role == "owner"
    assert ws.client_profile.stage == "onboarding"
    assert ws.client_profile.currency == "BZD"
    assert set(ServiceLine.objects.for_workspace(ws.id).values_list("kind", flat=True)) == {"social_media", "branding"}
    contact = Contact.objects.for_workspace(ws.id).get()
    assert contact.is_primary and contact.is_approver
    assert AuditEvent.objects.filter(workspace=ws, verb="client.created").exists()


def test_stage_change_is_audited_and_sets_start_date(studio):
    profile = profile_for(studio["skincare"])
    profile.stage = ClientProfile.Stage.ONBOARDING
    profile.save()
    assert change_stage(profile, ClientProfile.Stage.ACTIVE, studio["owner"])
    profile.refresh_from_db()
    assert profile.started_on == timezone.localdate()
    event = AuditEvent.objects.get(verb="client.stage_changed", workspace=studio["skincare"])
    assert event.actor == studio["owner"]
    assert event.data == {"from": "Onboarding", "to": "Active"}
    assert not change_stage(profile, ClientProfile.Stage.ACTIVE, studio["owner"])  # no-op


def test_owner_adds_a_client_from_the_form(client, studio):
    client.force_login(studio["owner"])
    response = client.post(
        reverse("clients:create"),
        {
            "name": "Vista Realty",
            "stage": "lead",
            "services": ["brand_strategy"],
            "contact_name": "",
            "contact_email": "",
            "industry": "Real estate",
        },
    )
    ws = Workspace.objects.get(name="Vista Realty")
    assert response.status_code == 302 and response["Location"] == _ws_url("clients:detail", ws)
    assert ws.organization == studio["org"]


def test_staff_without_manage_clients_cannot_add_clients(client, studio):
    client.force_login(studio["manager"])
    assert client.get(reverse("clients:create")).status_code == 403
    assert client.post(reverse("clients:create"), {"name": "Sneaky", "stage": "lead"}).status_code == 403
    assert not Workspace.objects.filter(name="Sneaky").exists()


# ── Client list ──────────────────────────────────────────────────────────────


def test_list_shows_only_clients_the_user_works_on(client, studio):
    client.force_login(studio["owner"])
    assert _listed(client.get(reverse("clients:list") + "?view=all")) == {"Casa Maya Restaurant", "Luma Skincare"}

    client.force_login(studio["editor"])  # only on the restaurant
    response = client.get(reverse("clients:list") + "?view=all")
    assert _listed(response) == {"Casa Maya Restaurant"}
    assert "Luma Skincare" not in response.content.decode()


def test_list_saved_views_filter_by_stage(client, studio):
    profile = profile_for(studio["skincare"])
    change_stage(profile, ClientProfile.Stage.PAUSED, studio["owner"])
    client.force_login(studio["owner"])
    assert _listed(client.get(reverse("clients:list"))) == {"Casa Maya Restaurant"}
    assert _listed(client.get(reverse("clients:list") + "?view=inactive")) == {"Luma Skincare"}


def test_contributor_does_not_see_client_records(client, studio):
    client.force_login(studio["contributor"])
    assert _listed(client.get(reverse("clients:list") + "?view=all")) == set()


def test_client_users_never_see_the_client_list(client, studio):
    client.force_login(studio["restaurant_client"])
    response = client.get(reverse("clients:list"))
    assert response.status_code == 302 and response["Location"] == reverse("client_portal:dashboard")


def test_staff_of_another_studio_cannot_list_or_open_our_clients(client, studio):
    from apps.organizations.models import Organization

    other_org = Organization.objects.create(name="Another Studio")
    rival = make_user("rival@other.test", other_org, "owner")
    join(rival, Workspace.objects.create(organization=other_org, name="Their Client"), "owner")
    client.force_login(rival)
    response = client.get(reverse("clients:list") + "?view=all")
    assert _listed(response) == {"Their Client"}
    assert "Casa Maya" not in response.content.decode()
    assert client.get(_ws_url("clients:detail", studio["restaurant"])).status_code == 403


# ── Client pages ─────────────────────────────────────────────────────────────

CLIENT_PAGES = ["clients:detail", "clients:contacts", "clients:services", "clients:activity"]


@pytest.mark.parametrize("name", CLIENT_PAGES)
def test_owner_opens_every_client_page(client, studio, name):
    client.force_login(studio["owner"])
    assert client.get(_ws_url(name, studio["restaurant"])).status_code == 200


@pytest.mark.parametrize("name", CLIENT_PAGES)
def test_client_user_cannot_open_client_pages_even_for_their_own_company(client, studio, name):
    client.force_login(studio["restaurant_client"])
    assert client.get(_ws_url(name, studio["restaurant"])).status_code == 403


@pytest.mark.parametrize("name", CLIENT_PAGES)
def test_cross_client_pages_are_forbidden(client, studio, name):
    # The editor works on the restaurant only; the skincare client is someone else's data.
    client.force_login(studio["editor"])
    assert client.get(_ws_url(name, studio["skincare"])).status_code == 403
    client.force_login(studio["restaurant_client"])
    assert client.get(_ws_url(name, studio["skincare"])).status_code == 403


def test_contributor_cannot_open_client_record(client, studio):
    client.force_login(studio["contributor"])
    assert client.get(_ws_url("clients:detail", studio["restaurant"])).status_code == 403


def test_activity_needs_internal_access(client, studio):
    client.force_login(studio["viewer"])  # sees the profile, not internal history
    assert client.get(_ws_url("clients:detail", studio["restaurant"])).status_code == 200
    assert client.get(_ws_url("clients:activity", studio["restaurant"])).status_code == 403


def test_overview_shows_retainer_health_and_upcoming_posts(client, studio):
    ws = studio["restaurant"]
    Retainer.objects.create(
        workspace=ws,
        name="Social retainer",
        monthly_fee=Decimal("2400"),
        starts_on=timezone.localdate(),
        posts_per_month=12,
    )
    account = make_account(ws, "Casa Maya")
    make_post(ws, account, status="scheduled", title="Weekend brunch menu")
    client.force_login(studio["owner"])
    body = client.get(_ws_url("clients:detail", ws)).content.decode()
    assert "Weekend brunch menu" in body
    assert "BZD 2,400.00" in body
    assert "of 12 posts" in body


def test_profile_update_saves_details_and_audits_stage(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["owner"])
    response = client.post(
        _ws_url("clients:profile_update", ws),
        {
            "stage": "paused",
            "legal_name": "Casa Maya Ltd.",
            "industry": "Restaurant",
            "website": "",
            "city": "Belize City",
            "country": "bz",
            "portal_language": "es",
            "currency": "BZD",
            "lead_source": "",
            "internal_notes": "Owner prefers WhatsApp",
        },
    )
    assert response.status_code == 302
    profile = ClientProfile.objects.get(workspace=ws)
    assert (profile.stage, profile.legal_name, profile.portal_language, profile.country) == (
        "paused",
        "Casa Maya Ltd.",
        "es",
        "BZ",
    )
    assert AuditEvent.objects.filter(workspace=ws, verb="client.stage_changed").exists()


def test_invalid_profile_reopens_the_form_with_errors(client, studio):
    client.force_login(studio["owner"])
    response = client.post(
        _ws_url("clients:profile_update", studio["restaurant"]),
        {"stage": "active", "currency": "XXX", "portal_language": "en"},
    )
    assert response.status_code == 400
    assert "field-error" in response.content.decode()


def test_editor_cannot_change_the_client_record(client, studio):
    client.force_login(studio["editor"])
    response = client.post(
        _ws_url("clients:profile_update", studio["restaurant"]),
        {"stage": "former", "currency": "BZD", "portal_language": "en"},
    )
    assert response.status_code == 403
    assert profile_for(studio["restaurant"]).stage == "active"


def test_internal_notes_never_reach_the_portal(client, studio):
    profile = profile_for(studio["restaurant"])
    profile.internal_notes = "Margin is thin on this one"
    profile.save()
    client.force_login(studio["restaurant_client"])
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(studio["restaurant"].id)
    session.save()
    for name in ("client_portal:dashboard", "client_portal:approval_queue"):
        response = client.get(reverse(name))
        assert response.status_code == 200
        assert "Margin is thin" not in response.content.decode()


# ── Contacts: ids from another client are a 404, never a write ───────────────


def test_add_and_edit_contacts(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["owner"])
    client.post(
        _ws_url("clients:contact_add", ws),
        {"name": "Rosa Itzá", "email": "rosa@casamaya.test", "is_billing": "on", "is_primary": "on"},
    )
    rosa = Contact.objects.get(name="Rosa Itzá")
    assert rosa.workspace == ws and rosa.is_billing and rosa.is_primary
    client.post(
        _ws_url("clients:contact_edit", ws, contact_id=studio["restaurant_contact"].id),
        {"name": "Ana Pérez", "is_primary": "on"},
    )
    rosa.refresh_from_db()
    assert not rosa.is_primary  # one main contact per client
    body = client.get(_ws_url("clients:contacts", ws)).content.decode()
    assert "Rosa Itzá" in body and "Lia Chen" not in body


def test_editing_another_clients_contact_through_my_client_is_404(client, studio):
    client.force_login(studio["owner"])  # owner of both, so only the id mismatch stops it
    foreign = studio["skincare_contact"]
    url = _ws_url("clients:contact_edit", studio["restaurant"], contact_id=foreign.id)
    assert client.post(url, {"name": "Hacked"}).status_code == 404
    delete_url = _ws_url("clients:contact_delete", studio["restaurant"], contact_id=foreign.id)
    assert client.post(delete_url).status_code == 404
    foreign.refresh_from_db()
    assert foreign.name == "Lia Chen"


def test_contacts_of_a_client_i_do_not_work_on_are_forbidden(client, studio):
    client.force_login(studio["manager"])  # manager of the restaurant only
    foreign = studio["skincare_contact"]
    assert (
        client.post(
            _ws_url("clients:contact_edit", studio["skincare"], contact_id=foreign.id), {"name": "Hacked"}
        ).status_code
        == 403
    )
    assert client.post(_ws_url("clients:contact_delete", studio["skincare"], contact_id=foreign.id)).status_code == 403
    assert Contact.objects.filter(pk=foreign.pk, name="Lia Chen").exists()


def test_client_user_cannot_touch_contacts(client, studio):
    client.force_login(studio["restaurant_client"])
    contact = studio["restaurant_contact"]
    assert client.post(_ws_url("clients:contact_add", studio["restaurant"]), {"name": "Me"}).status_code == 403
    assert (
        client.post(_ws_url("clients:contact_delete", studio["restaurant"], contact_id=contact.id)).status_code == 403
    )
    assert Contact.objects.filter(pk=contact.pk).exists()


# ── Services and retainer ────────────────────────────────────────────────────


def test_service_save_updates_existing_kind(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["manager"])
    client.post(_ws_url("clients:service_save", ws), {"kind": "social_media", "status": "active"})
    client.post(
        _ws_url("clients:service_save", ws), {"kind": "social_media", "status": "paused", "notes": "Low season"}
    )
    service = ServiceLine.objects.for_workspace(ws.id).get()
    assert (service.status, service.notes) == ("paused", "Low season")


def test_retainer_save(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["owner"])
    client.post(
        _ws_url("clients:retainer_save", ws),
        {
            "name": "Social retainer",
            "monthly_fee": "1800.00",
            "currency": "BZD",
            "starts_on": "2026-11-01",
            "posts_per_month": 16,
            "reels_per_month": 4,
            "stories_per_month": 20,
            "shoot_days_per_month": 1,
            "revision_rounds": 2,
        },
    )
    retainer = Retainer.current_for(ws)
    assert retainer.monthly_fee == Decimal("1800.00") and retainer.posts_per_month == 16


def test_cross_client_service_and_retainer_writes_are_forbidden(client, studio):
    client.force_login(studio["manager"])
    assert (
        client.post(
            _ws_url("clients:service_save", studio["skincare"]), {"kind": "branding", "status": "active"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            _ws_url("clients:retainer_save", studio["skincare"]), {"name": "x", "monthly_fee": "1", "currency": "BZD"}
        ).status_code
        == 403
    )
    assert not ServiceLine.objects.for_workspace(studio["skincare"].id).exists()


# ── Health ───────────────────────────────────────────────────────────────────


def test_health_flags_overdue_approvals_thin_runway_and_disconnected_accounts(studio):
    ws = studio["restaurant"]
    now = timezone.now()
    good = make_account(ws, "Casa Maya")
    make_account(ws, "Casa Maya Page", platform="facebook", status="disconnected")
    make_post(ws, good, status="pending_client", when=now - timedelta(hours=2), title="Late one")
    health = client_health(ws, now)
    by_key = {s.key: s for s in health.signals}
    assert by_key["approvals"].tone == "danger"
    assert by_key["runway"].tone == "danger"  # nothing planned ahead
    assert by_key["accounts"].tone == "danger" and "Casa Maya Page" in by_key["accounts"].detail
    assert health.tone == "danger"


def test_runway_counts_planned_posts_against_the_retainer(studio):
    ws = studio["restaurant"]
    now = timezone.now()
    account = make_account(ws, "Casa Maya")
    Retainer.objects.create(
        workspace=ws, name="Small", monthly_fee=Decimal("900"), starts_on=timezone.localdate(), posts_per_month=4
    )
    for day in (2, 9, 16, 23):
        make_post(ws, account, status="approved", when=now + timedelta(days=day))
    make_post(ws, account, status="published", when=now + timedelta(days=5))  # not "planned"
    make_post(ws, account, status="scheduled", when=now + timedelta(days=40))  # beyond 30 days
    planned, last = runway(ws, now)
    assert planned == 4
    assert last.date() == (now + timedelta(days=23)).date()
    assert {s.key: s for s in client_health(ws, now).signals}["runway"].tone == "success"
