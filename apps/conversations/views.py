"""The studio side of client messages, per client. Reading and replying need ``view_client_profile``;
billing topics need ``view_billing``. Clients see every message here."""

from __future__ import annotations

from django.contrib import messages as flash
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.conversations import services
from apps.conversations.models import Conversation
from apps.members.decorators import require_permission
from apps.studio.decorators import in_local_time
from apps.studio.permissions import workspace_permission


def _visible(request):
    qs = Conversation.objects.filter(workspace=request.workspace)
    if not workspace_permission(request, "view_billing"):
        qs = qs.exclude(about__in=Conversation.BILLING_TOPICS)
    return qs


@require_permission("view_client_profile")
@in_local_time
def client_messages(request, workspace_id, conversation_id=None):
    from apps.clients.views import _context as client_context

    conversations = list(_visible(request).prefetch_related("messages"))
    current = get_object_or_404(_visible(request), pk=conversation_id) if conversation_id else None
    if current:
        services.mark_read(current, request.user)
    return render(
        request,
        "conversations/client.html",
        {
            **client_context(request, "messages"),
            "conversations": conversations,
            "unread": {c.pk for c in conversations if services.unread_count([c], request.user)},
            "current": current,
            "messages_list": current.messages.select_related("author") if current else [],
        },
    )


@require_permission("view_client_profile")
@require_POST
def reply(request, workspace_id, conversation_id):
    conversation = get_object_or_404(_visible(request), pk=conversation_id)
    if request.POST.get("close") == "1":
        conversation.is_closed = True
        conversation.save(update_fields=["is_closed"])
        flash.success(request, "Conversation closed. It reopens if anyone writes again.")
    else:
        try:
            services.reply(conversation, request.user, request.POST.get("body", ""), from_client=False)
        except ValueError:
            flash.error(request, "Write a message first.")
    return redirect("conversations:thread", workspace_id=workspace_id, conversation_id=conversation.id)


@require_permission("view_client_profile")
@require_POST
def start(request, workspace_id):
    try:
        conversation = services.start(
            request.workspace, request.user, subject=request.POST.get("subject", ""), body=request.POST.get("body", ""),
            from_client=False,
        )  # fmt: skip
    except ValueError:
        flash.error(request, "Write a message first.")
        return redirect("conversations:client", workspace_id=workspace_id)
    return redirect("conversations:thread", workspace_id=workspace_id, conversation_id=conversation.id)
