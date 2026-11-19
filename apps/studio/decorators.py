import functools

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from apps.studio.permissions import has_studio_permission, is_studio_staff


def studio_staff_required(view_func):
    """Studio staff of the request's organization only (never a client on the portal)."""

    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        org = getattr(request, "org", None)
        if org is None or request.session.get("is_portal_session") or not is_studio_staff(request.user, org):
            raise PermissionDenied("Studio staff only.")
        return view_func(request, *args, **kwargs)

    return login_required(_wrapped)


def require_studio_permission(key):
    """An org-level studio permission (role or individual grant)."""

    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not has_studio_permission(getattr(request, "org_membership", None), key):
                raise PermissionDenied(f"Missing studio permission: {key}")
            return view_func(request, *args, **kwargs)

        return studio_staff_required(_wrapped)

    return decorator
