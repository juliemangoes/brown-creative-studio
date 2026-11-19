"""The studio shell: rail sections, client switcher and command menu."""

import json
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.studio.navigation import PRIMARY, is_installed
from apps.workspaces.models import Workspace


@pytest.fixture
def studio(db):
    org = Organization.objects.create(name="Brown Creative Studio")
    owner = User.objects.create_user(email="owner@studio.test", password="pw-123456789", tos_accepted_at=timezone.now())
    OrgMembership.objects.create(user=owner, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    restaurant = Workspace.objects.create(organization=org, name="Casa Maya Restaurant")
    skincare = Workspace.objects.create(organization=org, name="Luma Skincare")
    for ws in (restaurant, skincare):
        WorkspaceMembership.objects.create(
            user=owner, workspace=ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
    return {"org": org, "owner": owner, "restaurant": restaurant, "skincare": skincare}


def _rail(html: str) -> str:
    start = html.index('aria-label="Sections"')
    return html[start : html.index("</nav>", start)]


def _commands(html: str) -> list[dict]:
    m = re.search(r'<script id="studio-commands" type="application/json">(.*?)</script>', html, re.S)
    assert m, "command menu data missing"
    return json.loads(m[1])


def test_rail_shows_only_installed_sections(client, studio):
    client.force_login(studio["owner"])
    html = client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    rail = _rail(html)
    for section in PRIMARY:
        label = f'aria-label="{section.label}"'
        if is_installed(section):
            assert label in rail, section.key
        else:
            assert label not in rail, f"{section.key} shown before its app exists"
    # Phase 1: these BrightBean routes back the brief's sections.
    assert 'aria-label="Calendar"' in rail and 'aria-label="Approvals"' in rail and 'aria-label="Settings"' in rail


def test_active_section_is_marked_for_assistive_tech(client, studio):
    client.force_login(studio["owner"])
    url = reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})
    rail = _rail(client.get(url).content.decode())
    assert re.search(r'aria-label="Calendar"\s+class="sidebar-nav-item mb-0\.5 active" aria-current="page"', rail)
    rail = _rail(client.get(url + "?mode=list&tab=approvals").content.decode())
    assert re.search(r'aria-label="Approvals"\s+class="sidebar-nav-item mb-0\.5 active" aria-current="page"', rail)


def test_all_clients_scope_links_to_studio_wide_pages(client, studio):
    client.force_login(studio["owner"])
    html = client.get(reverse("organizations:cross_workspace_calendar")).content.decode()
    assert "All clients" in html
    rail = _rail(html)
    assert f'href="{reverse("organizations:cross_workspace_calendar")}"' in rail
    # Approvals needs a client until the studio-wide queue exists.
    assert 'aria-disabled="true"' in rail and "Choose a client to open approvals" in rail


def test_section_needing_a_permission_is_hidden(client, studio):
    contributor = User.objects.create_user(
        email="free@studio.test", password="pw-123456789", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=contributor, organization=studio["org"], org_role=OrgMembership.OrgRole.MEMBER)
    WorkspaceMembership.objects.create(
        user=contributor, workspace=studio["restaurant"], workspace_role=WorkspaceMembership.WorkspaceRole.CONTRIBUTOR
    )
    client.force_login(contributor)
    html = client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    rail = _rail(html)
    assert 'aria-label="Reports"' not in rail  # contributors lack view_analytics
    assert 'aria-label="Inbox"' not in rail  # and use_inbox


def test_switcher_and_command_menu_list_only_the_users_clients(client, studio):
    other_org = Organization.objects.create(name="Someone else")
    secret = Workspace.objects.create(organization=other_org, name="Secret Client")
    freelancer = User.objects.create_user(
        email="f@studio.test", password="pw-123456789", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=freelancer, organization=studio["org"], org_role=OrgMembership.OrgRole.MEMBER)
    WorkspaceMembership.objects.create(
        user=freelancer, workspace=studio["restaurant"], workspace_role=WorkspaceMembership.WorkspaceRole.EDITOR
    )
    client.force_login(freelancer)
    html = client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    assert "Casa Maya Restaurant" in html
    assert "Luma Skincare" not in html
    assert secret.name not in html
    labels = {c["label"] for c in _commands(html)}
    assert "Luma Skincare" not in labels and secret.name not in labels


def test_switching_client_keeps_the_section(client, studio):
    client.force_login(studio["owner"])
    html = client.get(reverse("analytics:index", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    commands = {c["label"]: c for c in _commands(html) if c["group"] == "Switch client"}
    assert commands["Luma Skincare"]["url"] == reverse(
        "analytics:index", kwargs={"workspace_id": studio["skincare"].id}
    )


def test_command_menu_offers_creation_and_theme(client, studio):
    client.force_login(studio["owner"])
    html = client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    commands = _commands(html)
    new_post = next(c for c in commands if c["label"] == "New post")
    assert new_post["url"] == reverse("composer:compose", kwargs={"workspace_id": studio["restaurant"].id})
    assert {c["theme"] for c in commands if "theme" in c} == {"light", "dark", "system"}


def test_theme_is_applied_before_first_paint(client, studio):
    client.force_login(studio["owner"])
    html = client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    head = html[: html.index("</head>")]
    assert "localStorage.getItem('studio-theme')" in head
    assert head.index("studio-theme") < head.index("styles.css")


def test_portal_sessions_get_no_studio_shell(client, studio):
    customer = User.objects.create_user(email="c@client.test", password="pw-123456789", tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(
        user=customer, workspace=studio["restaurant"], workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT
    )
    client.force_login(customer)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(studio["restaurant"].id)
    session.save()
    html = client.get(reverse("client_portal:dashboard")).content.decode()
    assert "studio-commands" not in html
    assert "Luma Skincare" not in html


def test_brightbean_review_card_and_connect_suggestions_are_gone(client, studio):
    client.force_login(studio["owner"])
    html = client.get(reverse("calendar:calendar", kwargs={"workspace_id": studio["restaurant"].id})).content.decode()
    assert "trustpilot" not in html.lower()
    assert 'class="sidebar-connect-channels"' not in html
