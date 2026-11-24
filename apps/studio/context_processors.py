"""Data for the studio shell: rail sections, the client switcher and the command menu."""

from django.urls import NoReverseMatch, reverse

from apps.studio.navigation import PRIMARY, SECONDARY, active_key, is_installed, section_url


def _is_client_scope(request) -> bool:
    match = getattr(request, "resolver_match", None)
    return bool(match and "workspace_id" in match.kwargs)


def _safe_reverse(name, **kwargs):
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch:
        return None


def shell(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or getattr(request, "session", {}).get("is_portal_session"):
        return {}

    in_client = _is_client_scope(request)
    workspace = getattr(request, "workspace", None) if in_client else None
    membership = getattr(request, "workspace_membership", None) if workspace else None
    permissions = membership.effective_permissions if membership else None
    current = active_key(request)

    def items(sections):
        rows = []
        for section in sections:
            if not is_installed(section):
                continue
            url = section_url(section, workspace, permissions)
            needs_client = workspace is None and url is None and section.client_url is not None
            if url is None and not needs_client:
                continue  # no route for this scope, or no permission
            rows.append(
                {
                    "key": section.key,
                    "label": section.label,
                    "icon": section.icon,
                    "url": url,
                    "active": section.key == current,
                    "needs_client": needs_client,
                }
            )
        return rows

    primary = items(PRIMARY)
    secondary = items(SECONDARY)

    # Client switcher: the same section for another client where that makes sense.
    current_section = next((s for s in PRIMARY + SECONDARY if s.key == current), None)
    all_clients_url = _safe_reverse("studio:today") or _safe_reverse("organizations:cross_workspace_calendar")
    clients = []
    from apps.members.models import WorkspaceMembership

    memberships = (
        WorkspaceMembership.objects.filter(user=user, workspace__is_archived=False)
        .select_related("workspace")
        .order_by("workspace__name")
    )
    for ws in (m.workspace for m in memberships):
        target = None
        if current_section and current_section.client_url:
            target = section_url(current_section, ws)
        clients.append(
            {
                "id": str(ws.id),
                "name": ws.name,
                "icon_url": ws.icon.url if getattr(ws, "icon", None) else "",
                "initial": (ws.name[:1] or "?").upper(),
                "url": target or _safe_reverse("calendar:calendar", workspace_id=ws.id),
                "current": bool(workspace and ws.id == workspace.id),
            }
        )

    commands = []
    for row in primary + secondary:
        if row["url"]:
            commands.append({"group": "Go to", "label": row["label"], "url": row["url"]})
    if all_clients_url:
        commands.append({"group": "Switch client", "label": "All clients", "url": all_clients_url})
    for client in clients:
        if not client["current"] and client["url"]:
            commands.append({"group": "Switch client", "label": client["name"], "url": client["url"]})
    can_plan = bool(permissions and permissions.get("view_plan", False))
    if workspace is not None and can_plan:
        plan_url = _safe_reverse("planning:board", workspace_id=workspace.id)
        if plan_url:
            commands.append({"group": "Go to", "label": "Plan", "url": plan_url})
    if workspace is not None:
        compose = _safe_reverse("composer:compose", workspace_id=workspace.id)
        if compose:
            commands.append({"group": "Create", "label": "New post", "url": compose})
        commands.append({"group": "Create", "label": "New idea", "event": "studio:new-idea"})
    for label, theme in (("Use light theme", "light"), ("Use dark theme", "dark"), ("Match system theme", "system")):
        commands.append({"group": "Theme", "label": label, "theme": theme})
    for label, name in (
        ("Notifications", "notifications:list"),
        ("Your settings", "accounts:settings"),
        ("Design system", "design_system:style_guide"),
        ("Source code", "about:source"),
        ("Sign out", "accounts:logout"),
    ):
        url = _safe_reverse(name)
        if url:
            commands.append({"group": "Account", "label": label, "url": url})

    return {
        "shell_primary": primary,
        "shell_secondary": secondary,
        "shell_clients": clients,
        "shell_current_client": workspace,
        "shell_all_clients_url": all_clients_url,
        "shell_in_all_clients": workspace is None,
        "shell_commands": commands,
        "shell_can_plan": can_plan,
    }


def portal(request):
    """Extra portal tabs and notices from the fork's apps (only on portal pages)."""
    if getattr(request, "portal_workspace", None) is None:
        return {}
    from apps.studio import portal as studio_portal

    current = getattr(getattr(request, "resolver_match", None), "namespace", "")
    return {
        "portal_sections": [
            {"label": s.label, "url": s.url, "active": current == s.key} for s in studio_portal.sections(request)
        ],
        "portal_notices": studio_portal.notices(request),
    }
