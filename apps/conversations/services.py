"""Start a conversation about an item, reply, mark read, notify the other side."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.conversations.models import Conversation, Message, ReadMark

log = logging.getLogger(__name__)
MAX_BODY = 5000


def resolve_about(workspace, about: str, about_id) -> tuple[str, object | None, str]:
    """The item a conversation is about, if it exists in this client and is visible to clients. (about, obj, label)."""
    if about not in Conversation.About.values or about == Conversation.About.GENERAL or not about_id:
        return Conversation.About.GENERAL, None, ""
    from apps.billing.models import Contract, Invoice, Proposal
    from apps.brand.models import BrandStage
    from apps.composer.models import Post
    from apps.projects.models import Project
    from apps.shoots.models import Shoot

    lookups = {
        "post": (Post.objects.filter(workspace=workspace), lambda o: o.title or (o.caption or "")[:60] or "Post"),
        "invoice": (Invoice.objects.filter(workspace=workspace).exclude(status="draft"), lambda o: o.number),
        "proposal": (Proposal.objects.filter(workspace=workspace).exclude(status="draft"), lambda o: o.title),
        "contract": (Contract.objects.filter(workspace=workspace).exclude(status="draft"), lambda o: o.title),
        "shoot": (Shoot.objects.filter(workspace=workspace).exclude(status="cancelled"), lambda o: o.title),
        "brand_stage": (BrandStage.objects.filter(workspace=workspace), lambda o: o.get_key_display()),
        "project": (Project.objects.filter(workspace=workspace), lambda o: o.name),
    }
    qs, label = lookups[about]
    obj = qs.filter(pk=about_id).first()
    if obj is None:
        return Conversation.About.GENERAL, None, ""
    return about, obj, str(label(obj))[:200]


@transaction.atomic
def start(workspace, user, *, subject, body, from_client, about="general", about_id=None) -> Conversation:
    body = (body or "").strip()[:MAX_BODY]
    if not body:
        raise ValueError("Write a message first.")
    about, obj, label = resolve_about(workspace, about, about_id)
    conversation = Conversation.objects.create(
        workspace=workspace,
        subject=(subject or "").strip()[:200] or label or "Question",
        about=about,
        about_id=obj.pk if obj else None,
        about_label=label,
        started_by=user,
    )
    _add(conversation, user, body, from_client)
    return conversation


@transaction.atomic
def reply(conversation, user, body, *, from_client) -> Message:
    body = (body or "").strip()[:MAX_BODY]
    if not body:
        raise ValueError("Write a message first.")
    if conversation.is_closed:
        conversation.is_closed = False
        conversation.save(update_fields=["is_closed"])
    return _add(conversation, user, body, from_client)


def _add(conversation, user, body, from_client) -> Message:
    message = Message.objects.create(conversation=conversation, author=user, body=body, from_client=from_client)
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message.created_at)
    mark_read(conversation, user)
    record(
        "message.sent",
        f"{'Client' if from_client else 'Studio'} message in “{conversation.subject}”",
        workspace=conversation.workspace,
        actor=user,
        target=conversation,
    )
    transaction.on_commit(lambda: _notify(conversation, message))
    return message


def mark_read(conversation, user) -> None:
    ReadMark.objects.update_or_create(conversation=conversation, user=user, defaults={"read_at": timezone.now()})


def unread_count(conversations, user) -> int:
    reads = dict(
        ReadMark.objects.filter(user=user, conversation__in=conversations).values_list("conversation_id", "read_at")
    )
    return sum(
        1 for c in conversations if c.last_message_at and (reads.get(c.pk) is None or reads[c.pk] < c.last_message_at)
    )


def _notify(conversation, message):
    """Client wrote: the studio staff on this client. Studio wrote: the client's portal users who can read it."""
    try:
        if message.from_client:
            from apps.members.models import WorkspaceMembership
            from apps.notifications.engine import notify

            key = "view_billing" if conversation.is_billing else "view_client_profile"
            for m in WorkspaceMembership.objects.filter(workspace=conversation.workspace).select_related(
                "user", "custom_role"
            ):
                perms = m.effective_permissions
                if m.workspace_role != "client" and perms.get(key, False):
                    notify(
                        user=m.user,
                        event_type="client_message",
                        title=f"{conversation.workspace.name}: {conversation.subject}",
                        body=message.body[:200],
                        data={"conversation_id": str(conversation.id), "workspace_id": str(conversation.workspace_id)},
                    )
        else:
            from django.utils.translation import gettext as _

            from apps.studio.portal import client_users, send_portal_email

            key = "portal_view_billing" if conversation.is_billing else "portal_view_content"
            for user in client_users(conversation.workspace, key):
                send_portal_email(
                    user,
                    conversation.workspace,
                    subject=_("New message: %(subject)s") % {"subject": conversation.subject},
                    template="conversations/email/reply",
                    context={"conversation": conversation, "message": message},
                )
    except Exception:
        log.exception("Message notification failed for %s", conversation.pk)
