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
CLIENT_NAMESPACES = frozenset({"client_portal", "about"})
CLIENT_VIEWS = frozenset({"accounts:logout", "accounts:accept_terms", "notifications:unsubscribe", "health_check"})
SKIPPED_PREFIXES = ("/static/", "/media/", "/admin/", "/api/", "/oauth/", "/webhooks/", "/.well-known/")


def _view_name(match) -> str:
    return f"{match.namespace}:{match.url_name}" if match.namespace else (match.url_name or "")


def is_client_only(request) -> bool:
    """The user has client memberships and nothing that makes them studio staff (cached per request)."""
    cached = getattr(request, "_studio_client_only", None)
    if cached is not None:
        return cached
    from apps.members.models import WorkspaceMembership

    user = request.user
    result = WorkspaceMembership.objects.filter(user=user, workspace_role="client").exists() and not is_studio_staff(
        user
    )
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
            return redirect("client_portal:dashboard")
        return None
