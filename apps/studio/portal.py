"""The fork's additions to BrightBean's client portal: extra sections, notices, access and links.

Portal pages use BrightBean's ``portal_auth_required`` (a magic-link or password
portal session for one client) and then check a portal permission key from
``apps.studio.permissions.PORTAL_KEYS`` on the member's role.

Sections and notices are contributed by apps through ``register_section`` and
``register_notice``; ``templates/client_portal/portal_base.html`` renders them
(the one hook added to that upstream template, see CHANGES.md).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied

_SECTIONS: list[Callable] = []
_NOTICES: list[Callable] = []
_UPCOMING: list[Callable] = []


@dataclass
class PortalSection:
    key: str
    label: str
    url: str
    order: int = 50


@dataclass
class PortalNotice:
    text: str
    url: str
    action: str


def register_section(func):
    """``func(request) -> PortalSection | None``."""
    _SECTIONS.append(func)
    return func


def register_notice(func):
    """``func(request) -> PortalNotice | None``. Shown at the top of portal pages."""
    _NOTICES.append(func)
    return func


@dataclass
class UpcomingItem:
    when: object  # date or datetime, in the client's time
    label: str
    url: str
    kind: str  # shoot | post | invoice | …


def register_upcoming(func):
    """``func(request) -> list[UpcomingItem]``. Shown on the portal home under "Coming up"."""
    _UPCOMING.append(func)
    return func


def upcoming(request, limit=8) -> list[UpcomingItem]:
    from datetime import date, datetime

    items = [i for f in _UPCOMING for i in (f(request) or [])]

    def key(item):
        w = item.when
        return w.date() if isinstance(w, datetime) else w if isinstance(w, date) else date.max

    return sorted(items, key=key)[:limit]


def portal_permissions(request) -> dict:
    membership = getattr(request, "portal_membership", None)
    return membership.effective_permissions if membership else {}


def sections(request) -> list[PortalSection]:
    found = [s for s in (f(request) for f in _SECTIONS) if s]
    return sorted(found, key=lambda s: s.order)


def notices(request) -> list[PortalNotice]:
    return [n for n in (f(request) for f in _NOTICES) if n]


def portal_permission_required(key):
    """Portal session (BrightBean's decorator) plus a portal permission key on the member's role."""
    from apps.client_portal.decorators import portal_auth_required

    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not portal_permissions(request).get(key, False):
                raise PermissionDenied(f"Portal permission needed: {key}")
            return view_func(request, *args, **kwargs)

        return portal_auth_required(_wrapped)

    return decorator


def portal_login_url(user, workspace) -> str:
    """A fresh single-use magic link into this client's portal, for an email."""
    from apps.client_portal.models import MagicLinkToken

    token = MagicLinkToken.objects.create(user=user, workspace=workspace)
    return f"{settings.APP_URL.rstrip('/')}/portal/{token.token}/"


def client_users(workspace, permission=None):
    """The workspace's client users, optionally only those whose portal role has ``permission``."""
    from apps.members.models import WorkspaceMembership

    memberships = WorkspaceMembership.objects.filter(
        workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT, user__is_active=True
    ).select_related("user", "custom_role")
    return [m.user for m in memberships if permission is None or m.effective_permissions.get(permission, False)]


def send_portal_email(user, workspace, *, subject, template, context) -> bool:
    """Email a client user with a fresh portal link, in their language. ``subject`` may be a callable
    (evaluated in that language). Returns False if it could not be sent (logged)."""
    import logging

    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from apps.common.mail import send_or_raise, transactional
    from apps.studio.languages import for_user

    ctx = {
        **context,
        "user": user,
        "workspace": workspace,
        "portal_url": portal_login_url(user, workspace),
        "site_name": settings.SITE_NAME,
    }
    with for_user(user, workspace):  # the recipient's language; ``subject`` may be a lazy string
        msg = EmailMultiAlternatives(
            subject=subject() if callable(subject) else str(subject),
            body=render_to_string(f"{template}.txt", ctx),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
            headers=transactional(),
        )
        msg.attach_alternative(render_to_string(f"{template}.html", ctx), "text/html")
    try:
        send_or_raise(msg)
    except Exception:
        logging.getLogger(__name__).exception("Portal email to %s failed", user.email)
        return False
    return True
