"""Who is acting, for audit entries written from signal handlers.

Signals do not see the request, so a middleware stores the user and IP for the
duration of each request. Background jobs (the publisher, reminders) have none,
and their entries record the system as the actor.
"""

from contextvars import ContextVar

_current: ContextVar[dict | None] = ContextVar("audit_actor", default=None)


def current_actor() -> dict:
    return _current.get() or {}


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None
        token = _current.set({"user": user if user is not None and user.is_authenticated else None, "ip": ip})
        try:
            return self.get_response(request)
        finally:
            _current.reset(token)
