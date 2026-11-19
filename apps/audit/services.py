"""Write audit entries: ``record(verb, summary, ...)``."""

import logging

from django.db import transaction

from apps.audit.context import current_actor
from apps.audit.models import AuditEvent

logger = logging.getLogger(__name__)


def _label(user) -> str:
    if user is None:
        return "System"
    return getattr(user, "name", "") or getattr(user, "email", "") or str(user)


def record(verb, summary, *, organization=None, workspace=None, actor=..., target=None, data=None, ip=...):
    """Append an audit entry. ``actor`` defaults to the request's user (or the system in background jobs).

    Never raises: an audit failure is logged and must not undo the action it describes.
    """
    context = current_actor()
    if actor is ...:
        actor = context.get("user")
    if organization is None and workspace is not None:
        organization = workspace.organization
    if ip is ...:
        ip = context.get("ip")
    try:
        # Own savepoint: a failed insert must not abort the caller's transaction.
        with transaction.atomic():
            return AuditEvent.objects.create(
                organization=organization,
                workspace=workspace,
                actor=actor,
                actor_label=_label(actor),
                verb=verb,
                target_type=type(target).__name__ if target is not None else "",
                target_id=str(getattr(target, "pk", "")) if target is not None else "",
                summary=summary[:2000],
                data=data or {},
                ip_address=ip,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not write audit event %s", verb)
        return None


def record_after_commit(verb, summary, *, organization_id, data=None):
    """For removals: write once the transaction commits, and only if the organization still exists.

    A membership deleted as part of deleting the whole organization must not leave
    an entry pointing at it (the delete would fail on the foreign key).
    """
    context = current_actor()
    actor, ip = context.get("user"), context.get("ip")

    def write():
        from apps.organizations.models import Organization

        organization = Organization.objects.filter(pk=organization_id).first()
        if organization is not None:
            record(verb, summary, organization=organization, actor=actor, data=data, ip=ip)

    transaction.on_commit(write)
