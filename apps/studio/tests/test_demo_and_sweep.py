"""The demo seed, and a sweep of every URL that takes a client (workspace) id."""

import uuid

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.urls.converters import UUIDConverter

from apps.accounts.models import User
from apps.clients.models import ClientProfile
from apps.composer.models import PlatformPost
from apps.organizations.models import Organization
from apps.studio.demo import DEMO_EMAIL_DOMAIN, DEMO_ORG_NAME, seed_demo
from apps.studio.today import build_today
from apps.workspaces.models import Workspace

# ── seed_demo ────────────────────────────────────────────────────────────────


@pytest.fixture
def demo(db):
    return seed_demo()


def _ws(name):
    return Workspace.objects.get(organization__name=DEMO_ORG_NAME, name=name)


def _user(local):
    return User.objects.get(email=f"{local}@{DEMO_EMAIL_DOMAIN}")


@override_settings(DEBUG=False)
def test_seed_refuses_without_debug(db):
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_demo")
    assert not Organization.objects.filter(name=DEMO_ORG_NAME).exists()


@override_settings(DEBUG=True)
def test_seed_refuses_twice_and_resets(db, capsys):
    call_command("seed_demo")
    with pytest.raises(CommandError, match="--reset"):
        call_command("seed_demo")
    call_command("seed_demo", "--reset")
    assert Organization.objects.filter(name=DEMO_ORG_NAME).count() == 1
    assert User.objects.filter(email__endswith=DEMO_EMAIL_DOMAIN).count() == 8
    out = capsys.readouterr().out
    assert "owner@brown-demo.example" in out and "/portal/" in out


def test_demo_has_three_clients_in_the_pipeline(demo):
    stages = dict(
        ClientProfile.objects.filter(workspace__organization=demo.organization).values_list("workspace__name", "stage")
    )
    assert stages == {"Casa Maya Restaurant": "active", "Luma Skincare": "active", "Vista Realty": "onboarding"}
    # Demo users joined the demo studio only (no leftover "My Organization").
    assert not Organization.objects.filter(
        name="My Organization", memberships__user__email__endswith=DEMO_EMAIL_DOMAIN
    ).exists()


def test_demo_posts_cover_every_state(demo):
    statuses = set(
        PlatformPost.objects.filter(post__workspace__organization=demo.organization).values_list("status", flat=True)
    )
    assert {
        "published",
        "scheduled",
        "approved",
        "pending_review",
        "pending_client",
        "changes_requested",
        "draft",
        "failed",
    } <= statuses


def test_today_shows_real_demo_items(demo):
    today = build_today(_user("owner"), demo.organization)
    assert today.approvals and today.approvals[0].overdue
    assert today.upcoming_count >= 3
    assert sorted(item.post.title for item in today.failed) == ["Sunscreen myths", "Weekend glow giveaway"]
    assert [t.platform_post.post.title for t in today.manual_posts] == ["How to layer serums"]
    assert {row["account"].account_name for row in today.accounts} == {"Luma Skincare", "Luma Skincare Page"}
    assert [row["workspace"].name for row in today.stalled] == ["Vista Realty"]
    assert {row["workspace"].name for row in today.gaps} >= {"Luma Skincare"}


def test_freelancer_sees_only_the_restaurant(demo):
    today = build_today(_user("diego"), demo.organization)
    assert {ws.name for ws in today.workspaces} == {"Casa Maya Restaurant"}


def test_demo_portal_shows_own_client_only_and_no_internal_notes(client, demo):
    client.force_login(_user("lia"))
    response = client.get(reverse("dashboard"), follow=True)
    assert response.status_code == 200
    queue = client.get(reverse("client_portal:approval_queue")).content.decode()
    assert "Something new is coming on the 1st." in queue  # the portal shows captions
    for leak in ("Casa Maya", "net 30", "invoice clears", "Launching a sunscreen line"):
        assert leak not in queue
    restaurant = _ws("Casa Maya Restaurant")
    assert client.get(reverse("calendar:calendar", kwargs={"workspace_id": restaurant.id})).status_code == 403


# ── Sweep: every URL with a workspace id refuses another client's user ───────


def _converters(pattern):
    try:
        return dict(pattern.pattern.converters)
    except AttributeError:  # regex patterns
        return {"_regex": None} if pattern.pattern.regex.groups else {}


def _workspace_patterns(resolver=None, prefix_names=(), inherited=None):
    """(name, converters) for every named URL, with converters from parent prefixes included."""
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        converters = {**(inherited or {}), **_converters(entry)}
        if isinstance(entry, URLResolver):
            namespace = (*prefix_names, entry.namespace) if entry.namespace else prefix_names
            yield from _workspace_patterns(entry, namespace, converters)
        elif isinstance(entry, URLPattern) and entry.name:
            yield ":".join((*prefix_names, entry.name)), converters


def _uuid_only_names():
    """URL names whose every argument is a UUID and that include workspace_id."""
    names = set()
    for name, converters in _workspace_patterns():
        if "workspace_id" in converters and all(isinstance(c, UUIDConverter) for c in converters.values()):
            names.add((name, tuple(sorted(converters))))
    return sorted(names)


def _kwargs(args, workspace):
    return {arg: (workspace.id if arg == "workspace_id" else uuid.uuid4()) for arg in args}


@pytest.fixture
def two_clients(db):
    from apps.studio.tests.helpers import make_studio

    return make_studio()


def test_sweep_covers_a_meaningful_number_of_urls():
    names = [name for name, _ in _uuid_only_names()]
    assert len(names) > 80
    assert "clients:detail" in names and "studio:today_client" in names


def test_sweep_another_clients_user_is_refused_everywhere(client, two_clients):
    """The skincare client, and a studio editor who only works on the restaurant, get 403 on every skincare URL."""
    target = two_clients["skincare"]
    for person in ("restaurant_client", "editor"):
        client.force_login(two_clients[person])
        leaks = []
        for name, args in _uuid_only_names():
            try:
                url = reverse(name, kwargs=_kwargs(args, target))
            except Exception:  # noqa: BLE001 - a name shared by several patterns
                continue
            for method in (client.get, client.post):
                status = method(url).status_code
                if status not in (403, 404):
                    leaks.append((person, method.__name__, name, status))
        assert not leaks, leaks
