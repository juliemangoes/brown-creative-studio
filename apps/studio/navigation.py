"""The studio's left rail, client switcher and command menu, built from one registry.

Each section names the URL it opens for one client and for "All clients".
A section appears only when its URL exists, so a module lights up in the rail
the moment its app is installed (Today in Phase 2, Shoots in Phase 4, …)
without touching the shell templates. Permission keys are the existing
BrightBean workspace permissions (``apps.members.models``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    icon: str
    client_url: str | None = None  # URL name taking workspace_id
    client_query: str = ""
    all_url: str | None = None  # URL name with no arguments
    permission: str | None = None  # workspace permission key needed for the client URL
    match: Callable[[str, str, str], bool] | None = None  # (app_name, url_name, tab) -> active?


def _app(*names: str) -> Callable[[str, str, str], bool]:
    return lambda app, url, tab: app in names


PRIMARY: tuple[Section, ...] = (
    Section("today", "Today", "today", client_url="studio:today_client", all_url="studio:today", match=_app("studio")),
    Section(
        "clients",
        "Clients",
        "clients",
        client_url="clients:detail",
        all_url="clients:list",
        permission="view_client_profile",
        match=_app("clients"),
    ),
    Section(
        "projects",
        "Projects",
        "projects",
        client_url="projects:list",
        all_url="projects:all",
        permission="view_projects",
        match=_app("projects"),
    ),
    Section(
        "calendar",
        "Calendar",
        "calendar",
        client_url="calendar:calendar",
        all_url="organizations:cross_workspace_calendar",
        match=lambda app, url, tab: (
            (app == "calendar" and tab != "approvals")
            or app == "planning"
            or url == "cross_workspace_calendar"
            or (app == "composer" and url in ("compose", "compose_edit"))
        ),
    ),
    Section(
        "research", "Research", "research", client_url="research:index", all_url="research:all", match=_app("research")
    ),
    Section("shoots", "Shoots", "shoots", client_url="shoots:list", all_url="shoots:all", match=_app("shoots")),
    Section(
        "approvals",
        "Approvals",
        "approvals",
        client_url="calendar:calendar",
        client_query="?mode=list&tab=approvals",
        match=lambda app, url, tab: (app == "calendar" and tab == "approvals") or app in ("approvals", "reviews"),
    ),
    Section(
        "reports",
        "Reports",
        "reports",
        client_url="analytics:index",
        all_url="reports:all",
        permission="view_analytics",
        match=_app("analytics", "reports"),
    ),
    Section(
        "billing",
        "Billing",
        "billing",
        client_url="billing:client",
        all_url="billing:index",
        permission="view_billing",
        match=_app("billing"),
    ),
    Section(
        "settings",
        "Settings",
        "settings",
        client_url="workspaces:settings",
        all_url="organizations:settings",
        match=_app(
            "workspaces",
            "organizations",
            "members",
            "api_keys",
            "settings_manager",
            "accounts",
            "social_accounts",
            "client_portal_admin",
        ),
    ),
)

# BrightBean features that are not in the brief's rail but must stay reachable.
SECONDARY: tuple[Section, ...] = (
    Section(
        "ideas",
        "Ideas",
        "ideas",
        client_url="composer:create_landing",
        match=lambda app, url, tab: app == "composer" and url not in ("compose", "compose_edit"),
    ),
    Section(
        "media",
        "Media",
        "media",
        client_url="media_library:index",
        all_url="media_library_org:shared_index",
        match=_app("media_library", "media_library_org", "assets"),
    ),
    Section("inbox", "Inbox", "inbox", client_url="inbox:feed", permission="use_inbox", match=_app("inbox")),
)


def _reverse(name: str | None, **kwargs) -> str | None:
    if not name:
        return None
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch:
        return None


def is_installed(section: Section) -> bool:
    """A section exists once either of its URLs resolves (checked with a placeholder id)."""
    if section.all_url and _reverse(section.all_url):
        return True
    return bool(
        section.client_url and _reverse(section.client_url, workspace_id="00000000-0000-0000-0000-000000000000")
    )


def section_url(section: Section, workspace=None, permissions: dict | None = None) -> str | None:
    """Where the section leads for ``workspace`` (a client) or, when None, for all clients."""
    if workspace is not None:
        if section.permission and permissions is not None and not permissions.get(section.permission, False):
            return None
        url = _reverse(section.client_url, workspace_id=workspace.id)
        return f"{url}{section.client_query}" if url else None
    return _reverse(section.all_url)


def active_key(request) -> str | None:
    match = getattr(request, "resolver_match", None)
    if match is None:
        return None
    app, url, tab = match.app_name or match.namespace or "", match.url_name or "", request.GET.get("tab", "")
    for section in PRIMARY + SECONDARY:
        if section.match and section.match(app, url, tab):
            return section.key
    return None
