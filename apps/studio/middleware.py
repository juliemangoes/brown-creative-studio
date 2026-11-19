"""Keep client users in the portal.

BrightBean's RBAC middleware lets any workspace member into that workspace's
pages, and portal invitations make clients organization members too. Without
this, a client signing in with a password would see the studio for their own
workspace, internal notes included. This middleware runs after
``apps.members.middleware.RBACMiddleware`` and:

- inside a workspace URL, lets a member with the client role reach only the
  approval and comment endpoints the portal itself posts to;
- elsewhere, sends users whose every membership is a client one to the portal.

Users with no organization yet (a new studio owner) are not affected.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from apps.studio.permissions import is_client_membership, is_studio_staff

# Workspace URLs the portal uses (approval actions, comments, attachments, version diff).
CLIENT_WORKSPACE_VIEWS = frozenset(
    {
        "approvals:approve",
        "approvals:request_changes",
        "approvals:reject",
        "approvals:resume",
        "approvals:add_comment",
        "approvals:edit_comment",
        "approvals:delete_comment",
        "approvals:comment_attachment",
        "approvals:version_diff",
    }
)
# Portal namespaces: BrightBean's portal, the public about pages, and the fork's portal sections.
CLIENT_NAMESPACES = frozenset({"client_portal", "about", "onboarding_portal", "brand_portal", "projects_portal"})
CLIENT_VIEWS = frozenset(
    {
        "accounts:logout",
        "accounts:accept_terms",
        "notifications:unsubscribe",
        "health_check",
        # Signed file links check the client's access themselves.
        "studio:file",
        # Onboarding step 8 sends clients to BrightBean's account-connection page.
        "onboarding:connection_page",
        "onboarding:connection_oauth_start",
        "onboarding:connection_done",
        "onboarding:connection_bluesky",
        "onboarding:connection_mastodon",
        "onboarding:oauth_callback",
    }
)
SKIPPED_PREFIXES = ("/static/", "/media/", "/admin/", "/api/", "/oauth/", "/webhooks/", "/.well-known/")


def _view_name(match) -> str:
    return f"{match.namespace}:{match.url_name}" if match.namespace else (match.url_name or "")


def is_client_only(request) -> bool:
    """In every studio where the user holds a client role, they have no staff role (cached per request).

    Judged per organization: BrightBean gives every new account a personal
    "My Organization", so owning *an* organization says nothing about this one.
    """
    cached = getattr(request, "_studio_client_only", None)
    if cached is not None:
        return cached
    from apps.members.models import WorkspaceMembership

    user = request.user
    client_org_ids = set(
        WorkspaceMembership.objects.filter(user=user, workspace_role="client").values_list(
            "workspace__organization_id", flat=True
        )
    )
    result = bool(client_org_ids) and not any(is_studio_staff(user, org_id) for org_id in client_org_ids)
    request._studio_client_only = result
    return result


class ClientScopeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith(SKIPPED_PREFIXES):
            return None
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        match = getattr(request, "resolver_match", None)
        if match is None:
            return None
        name = _view_name(match)
        if (
            match.namespace in CLIENT_NAMESPACES
            or name in CLIENT_VIEWS
            or name.startswith(("account_", "socialaccount_"))
        ):
            return None

        if "workspace_id" in view_kwargs:
            membership = getattr(request, "workspace_membership", None)
            if is_client_membership(membership) and name not in CLIENT_WORKSPACE_VIEWS:
                raise PermissionDenied("Clients use the portal for this workspace.")
            return None

        if is_client_only(request):
            _open_portal_session(request)
            return redirect("client_portal:dashboard")
        return None


def _open_portal_session(request) -> None:
    """A client who signed in with their password gets the portal session a magic link would give.

    BrightBean's portal only recognises magic-link sessions; without this a
    client signing in normally would land on "link expired". The workspace is
    their last client workspace, else their first; the portal re-checks the
    membership on every request.
    """
    if request.session.get("is_portal_session"):
        return
    from apps.members.models import WorkspaceMembership

    memberships = WorkspaceMembership.objects.filter(
        user=request.user, workspace_role="client", workspace__is_archived=False
    ).order_by("workspace__name")
    last = getattr(request.user, "last_workspace_id", None)
    membership = (last and memberships.filter(workspace_id=last).first()) or memberships.first()
    if membership is None:
        return
    request.session["is_portal_session"] = True
    request.session["portal_workspace_id"] = str(membership.workspace_id)
