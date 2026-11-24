"""Messages in the client portal. Needs ``portal_view_content``; billing topics also need ``portal_view_billing``."""

from __future__ import annotations

from django.contrib import messages as flash
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods, require_POST

from apps.conversations import services
from apps.conversations.models import Conversation
from apps.studio.portal import PortalSection, portal_permission_required, portal_permissions, register_section

KEY = "portal_view_content"


def visible(request):
    qs = Conversation.objects.filter(workspace=request.portal_workspace)
    if not portal_permissions(request).get("portal_view_billing"):
        qs = qs.exclude(about__in=Conversation.BILLING_TOPICS)
    return qs


@register_section
def _section(request):
    if not portal_permissions(request).get(KEY):
        return None
    conversations = list(visible(request))
    unread = services.unread_count(conversations, request.user) if conversations else 0
    label = gettext_lazy("Messages")
    return PortalSection(
        "conversations_portal",
        f"{label} ({unread})" if unread else label,
        reverse("conversations_portal:index"),
        order=90,
    )


@portal_permission_required(KEY)
@require_http_methods(["GET", "POST"])
def index(request):
    about, about_id = request.GET.get("about", "general"), request.GET.get("id") or None
    if about in Conversation.BILLING_TOPICS and not portal_permissions(request).get("portal_view_billing"):
        about, about_id = "general", None
    if request.method == "POST":
        about, about_id = request.POST.get("about", "general"), request.POST.get("about_id") or None
        if about in Conversation.BILLING_TOPICS and not portal_permissions(request).get("portal_view_billing"):
            about, about_id = "general", None
        try:
            conversation = services.start(
                request.portal_workspace,
                request.user,
                subject=request.POST.get("subject", ""),
                body=request.POST.get("body", ""),
                from_client=True,
                about=about,
                about_id=about_id,
            )
        except ValueError:
            flash.error(request, _("Write a message first."))
            return redirect("conversations_portal:index")
        flash.success(request, _("Sent. The studio usually replies within a working day."))
        return redirect("conversations_portal:thread", conversation_id=conversation.id)
    conversations = list(visible(request).prefetch_related("messages"))
    _about, obj, label = services.resolve_about(request.portal_workspace, about, about_id)
    return render(
        request,
        "conversations/portal/index.html",
        {
            "workspace": request.portal_workspace,
            "conversations": conversations,
            "unread": {c.pk for c in conversations if services.unread_count([c], request.user)},
            "about": _about,
            "about_id": obj.pk if obj else "",
            "about_label": label,
        },
    )


@portal_permission_required(KEY)
def thread(request, conversation_id):
    conversation = get_object_or_404(visible(request), pk=conversation_id)
    services.mark_read(conversation, request.user)
    return render(
        request,
        "conversations/portal/thread.html",
        {
            "workspace": request.portal_workspace,
            "conversation": conversation,
            "messages_list": conversation.messages.select_related("author"),
        },
    )


@portal_permission_required(KEY)
@require_POST
def reply(request, conversation_id):
    conversation = get_object_or_404(visible(request), pk=conversation_id)
    try:
        services.reply(conversation, request.user, request.POST.get("body", ""), from_client=True)
    except ValueError:
        flash.error(request, _("Write a message first."))
    return redirect("conversations_portal:thread", conversation_id=conversation.id)
