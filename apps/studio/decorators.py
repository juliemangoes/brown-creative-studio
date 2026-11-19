import functools
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.utils import timezone

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


def zone(name: str | None):
    """A ZoneInfo for ``name``, falling back to the studio's time zone."""
    for candidate in (name, settings.STUDIO_TIME_ZONE, "UTC"):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def in_local_time(view_func):
    """Render times in the client's time zone on client pages, else in the studio's.

    BrightBean keeps Django's TIME_ZONE at UTC and converts per view; without this
    a post at 17:00 in Belize would read 23:00.
    """

    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        workspace = getattr(request, "workspace", None) if "workspace_id" in kwargs else None
        with timezone.override(zone(workspace.effective_timezone if workspace else None)):
            return view_func(request, *args, **kwargs)

    return _wrapped


def in_studio_time(view_func):
    """Render times in the studio's time zone (pages that span clients)."""

    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        with timezone.override(zone(None)):
            return view_func(request, *args, **kwargs)

    return _wrapped
