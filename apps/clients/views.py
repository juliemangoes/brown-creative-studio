"""Client list and client pages.

Every page with a client in the URL is resolved by BrightBean's RBAC middleware
(403 for non-members) and checks a workspace permission here. Contacts,
services and retainers are always fetched through the URL's workspace, so an id
from another client is a 404, never a leak.
"""

from __future__ import annotations

from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.audit.models import AuditEvent
from apps.clients.forms import ContactForm, NewClientForm, ProfileForm, RetainerForm, ServiceForm
from apps.clients.health import client_health, runway
from apps.clients.models import ClientProfile, Contact, Retainer, ServiceLine
from apps.clients.services import change_stage, create_client, profile_for
from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.studio.decorators import in_local_time, in_studio_time, require_studio_permission, studio_staff_required
from apps.studio.permissions import has_studio_permission, workspace_permission
from apps.studio.today import posts_by_post

SAVED_VIEWS = [
    ("active", "Active", [ClientProfile.Stage.ACTIVE]),
    ("onboarding", "Onboarding", [ClientProfile.Stage.ONBOARDING]),
    ("pipeline", "Leads and proposals", [ClientProfile.Stage.LEAD, ClientProfile.Stage.PROPOSAL_SENT]),
    ("inactive", "Paused and former", [ClientProfile.Stage.PAUSED, ClientProfile.Stage.FORMER]),
    ("all", "All clients", list(ClientProfile.Stage.values)),
]
STAGE_TONES = {
    "lead": "neutral",
    "proposal_sent": "info",
    "onboarding": "accent",
    "active": "success",
    "paused": "warning",
    "former": "neutral",
}


def _visible_memberships(request):
    """The user's client workspaces in this studio where they may see the client record."""
    memberships = (
        WorkspaceMembership.objects.filter(
            user=request.user, workspace__organization=request.org, workspace__is_archived=False
        )
        .select_related("workspace", "custom_role")
        .order_by("workspace__name")
    )
    return [m for m in memberships if m.effective_permissions.get("view_client_profile", False)]


@studio_staff_required
@in_studio_time
def client_list(request):
    view_key = request.GET.get("view", "active")
    view = next((v for v in SAVED_VIEWS if v[0] == view_key), SAVED_VIEWS[0])
    now = timezone.now()
    rows, counts = [], {key: 0 for key, _, _ in SAVED_VIEWS}
    for membership in _visible_memberships(request):
        ws = membership.workspace
        profile = profile_for(ws)
        for key, _, stages in SAVED_VIEWS:
            counts[key] += profile.stage in stages
        if profile.stage not in view[2]:
            continue
        health = client_health(ws, now, include_billing=membership.effective_permissions.get("view_billing", False))
        planned, _ = runway(ws, now)
        rows.append(
            {
                "workspace": ws,
                "profile": profile,
                "stage_tone": STAGE_TONES[profile.stage],
                "services": list(ServiceLine.objects.for_workspace(ws.id).exclude(status=ServiceLine.Status.ENDED)),
                "retainer": Retainer.current_for(ws),
                "health": health,
                "planned": planned,
            }
        )
    return render(
        request,
        "clients/list.html",
        {
            "rows": rows,
            "saved_views": [(key, label, counts[key]) for key, label, _ in SAVED_VIEWS],
            "view_key": view[0],
            "view_label": view[1],
            "can_add": has_studio_permission(request.org_membership, "manage_clients"),
        },
    )


@require_studio_permission("manage_clients")
@require_http_methods(["GET", "POST"])
def client_create(request):
    form = NewClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        workspace = create_client(
            request.org,
            request.user,
            name=data["name"],
            stage=data["stage"],
            services=data["services"],
            contact_name=data["contact_name"],
            contact_email=data["contact_email"],
            industry=data["industry"],
        )
        messages.success(request, f"{workspace.name} was added.")
        return redirect("clients:detail", workspace_id=workspace.id)
    return render(request, "clients/new.html", {"form": form})


def _context(request, tab):
    ws = request.workspace
    profile = profile_for(ws)
    return {
        "workspace": ws,
        "profile": profile,
        "stage_tone": STAGE_TONES[profile.stage],
        "tab": tab,
        "can_edit": workspace_permission(request, "manage_client_profile"),
        "health": client_health(ws, include_billing=workspace_permission(request, "view_billing")),
    }


def _overview(request, profile_form=None, status=200):
    from apps.composer.models import PlatformPost
    from apps.social_accounts.models import SocialAccount

    ctx = _context(request, "overview")
    ws = request.workspace
    today = timezone.localdate()
    month_start = timezone.make_aware(datetime.combine(today.replace(day=1), time.min))
    delivered = (
        PlatformPost.objects.filter(post__workspace=ws, status="published", published_at__gte=month_start)
        .values("post_id")
        .distinct()
        .count()
    )
    upcoming = posts_by_post(
        PlatformPost.objects.filter(post__workspace=ws, scheduled_at__gte=timezone.now())
        .exclude(status__in=["published", "failed"])
        .select_related("post__workspace", "social_account")
        .order_by("scheduled_at", "created_at")[:40]  # platforms in the order they were added
    )[:6]
    if profile_form is None and ctx["can_edit"]:
        profile_form = ProfileForm(instance=ctx["profile"])
    ctx.update(
        {
            "retainer": Retainer.current_for(ws),
            "delivered_posts": delivered,
            "services": ServiceLine.objects.for_workspace(ws.id),
            "contacts": Contact.objects.for_workspace(ws.id)[:4],
            "accounts": SocialAccount.objects.for_workspace(ws.id).order_by("platform"),
            "upcoming": upcoming,
            "profile_form": profile_form,
        }
    )
    return render(request, "clients/overview.html", ctx, status=status)


@require_permission("view_client_profile")
@in_local_time
def client_detail(request, workspace_id):
    return _overview(request)


@require_permission("manage_client_profile")
@require_POST
@in_local_time
def profile_update(request, workspace_id):
    profile = profile_for(request.workspace)
    previous_stage = profile.stage
    form = ProfileForm(request.POST, instance=profile)
    if not form.is_valid():
        # Re-open the edit panel with the errors next to their fields.
        return _overview(request, profile_form=form, status=400)
    new_stage = form.cleaned_data["stage"]
    profile = form.save(commit=False)
    profile.stage = previous_stage  # stage changes go through change_stage for the audit entry
    profile.save()
    change_stage(profile, new_stage, request.user)
    messages.success(request, "Client details saved.")
    return redirect("clients:detail", workspace_id=workspace_id)


@require_permission("view_client_profile")
@in_local_time
def client_contacts(request, workspace_id):
    ctx = _context(request, "contacts")
    contacts = list(Contact.objects.for_workspace(request.workspace.id).order_by("-is_primary", "name"))
    rows = [
        (contact, ContactForm(instance=contact, auto_id=f"c{contact.pk.hex[:8]}_%s") if ctx["can_edit"] else None)
        for contact in contacts
    ]
    ctx.update({"rows": rows, "form": ContactForm(auto_id="new_%s") if ctx["can_edit"] else None})
    return render(request, "clients/contacts.html", ctx)


def _contact(request, contact_id):
    # Always through the URL's workspace: another client's contact id is a 404.
    return get_object_or_404(Contact.objects.for_workspace(request.workspace.id), pk=contact_id)


@require_permission("manage_client_profile")
@require_POST
def contact_save(request, workspace_id, contact_id=None):
    contact = _contact(request, contact_id) if contact_id else Contact(workspace=request.workspace)
    form = ContactForm(request.POST, instance=contact)
    if form.is_valid():
        saved = form.save()
        if saved.is_primary:
            Contact.objects.for_workspace(request.workspace.id).exclude(pk=saved.pk).update(is_primary=False)
        messages.success(request, f"{saved.name} saved.")
    else:
        messages.error(request, "Check the contact's name and email.")
    return redirect("clients:contacts", workspace_id=workspace_id)


@require_permission("manage_client_profile")
@require_POST
def contact_delete(request, workspace_id, contact_id):
    contact = _contact(request, contact_id)
    name = contact.name
    contact.delete()
    messages.success(request, f"{name} removed.")
    return redirect("clients:contacts", workspace_id=workspace_id)


@require_permission("view_client_profile")
@in_local_time
def client_services(request, workspace_id):
    ctx = _context(request, "services")
    ws = request.workspace
    retainer = Retainer.current_for(ws)
    ctx.update(
        {
            "services": ServiceLine.objects.for_workspace(ws.id),
            "retainer": retainer,
            "service_form": ServiceForm() if ctx["can_edit"] else None,
            "retainer_form": RetainerForm(instance=retainer) if ctx["can_edit"] else None,
        }
    )
    return render(request, "clients/services.html", ctx)


@require_permission("manage_client_profile")
@require_POST
def service_save(request, workspace_id):
    ws = request.workspace
    kind = request.POST.get("kind", "")
    existing = ServiceLine.objects.for_workspace(ws.id).filter(kind=kind).first()
    form = ServiceForm(request.POST, instance=existing or ServiceLine(workspace=ws))
    if form.is_valid():
        form.save()
        messages.success(request, "Service saved.")
    else:
        messages.error(request, "Check the service details.")
    return redirect("clients:services", workspace_id=workspace_id)


@require_permission("manage_client_profile")
@require_POST
def retainer_save(request, workspace_id):
    ws = request.workspace
    retainer = Retainer.current_for(ws) or Retainer(workspace=ws)
    form = RetainerForm(request.POST, instance=retainer)
    if form.is_valid():
        form.save()
        messages.success(request, "Retainer saved.")
    else:
        messages.error(request, "Check the retainer details.")
    return redirect("clients:services", workspace_id=workspace_id)


@require_permission("view_client_profile")
@in_local_time
def client_activity(request, workspace_id):
    if not workspace_permission(request, "view_internal"):
        raise PermissionDenied
    ctx = _context(request, "activity")
    ctx["events"] = AuditEvent.objects.filter(workspace=request.workspace).select_related("actor")[:100]
    return render(request, "clients/activity.html", ctx)
