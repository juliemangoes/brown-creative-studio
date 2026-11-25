"""Research in the studio: the weekly brief, the research profile and the swipe file.

``view_research`` opens the pages; ``manage_research`` changes the profile,
saves to the swipe file, prepares a brief and acts on recommendations.
Every query is scoped to the workspace in the URL. Research is studio-only:
nothing here reaches the portal.
"""

from __future__ import annotations

from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.audit.services import record
from apps.clients.forms import _style
from apps.members.decorators import require_permission
from apps.research import services, sources
from apps.research.models import ResearchBrief, ResearchItem, ResearchProfile, SwipeItem
from apps.studio.decorators import in_local_time, in_studio_time, studio_staff_required
from apps.studio.permissions import workspace_permission
from apps.studio.today import staff_memberships


def _can_manage(request) -> bool:
    return workspace_permission(request, "manage_research")


def _require_manage(request):
    if not _can_manage(request):
        raise PermissionDenied("Permission denied: manage_research")


# ── All clients ──────────────────────────────────────────────────────────────


@studio_staff_required
@in_studio_time
def all_clients(request):
    memberships = [
        m for m in staff_memberships(request.user, request.org) if m.effective_permissions.get("view_research", False)
    ]
    rows = []
    for m in memberships:
        brief = services.latest_brief(m.workspace)
        rows.append({"workspace": m.workspace, "brief": brief,
                     "new": brief.items.filter(status=ResearchItem.Status.NEW).count() if brief else 0})  # fmt: skip
    return render(request, "research/all.html", {"rows": rows, "this_week": services.week_start()})


# ── One client: the brief ────────────────────────────────────────────────────


def _brief_context(request, brief):
    items = list(brief.items.select_related("idea", "post")) if brief else []
    groups = [(value, label, [i for i in items if i.group == value]) for value, label in ResearchItem.Group.choices]
    return {
        "brief": brief,
        "groups": groups,
        "previous": ResearchBrief.objects.filter(workspace=request.workspace).exclude(pk=getattr(brief, "pk", None))[
            :8
        ],
        "track": services.track_record(request.workspace),
        "can_manage": _can_manage(request),
        "creative_center": sources.CREATIVE_CENTER_URL,
        "source_label": sources.source().label,
        "plan_date": timezone.localdate() + timedelta(days=7),
    }


@require_permission("view_research")
@in_local_time
@require_GET
def index(request, workspace_id):
    return render(
        request,
        "research/brief.html",
        {"workspace": request.workspace, **_brief_context(request, services.latest_brief(request.workspace))},
    )


@require_permission("view_research")
@in_local_time
@require_GET
def brief_detail(request, workspace_id, brief_id):
    brief = get_object_or_404(ResearchBrief, pk=brief_id, workspace=request.workspace)
    return render(request, "research/brief.html", {"workspace": request.workspace, **_brief_context(request, brief)})


@require_permission("view_research")
@require_POST
def generate(request, workspace_id):
    _require_manage(request)
    brief = services.generate_brief(request.workspace, request.user)
    messages.success(
        request, f"Brief prepared: {brief.items.count()} recommendations. Check each one before acting on it."
    )
    return redirect("research:index", workspace_id=workspace_id)


def _item(request, item_id) -> ResearchItem:
    return get_object_or_404(ResearchItem.objects.select_related("brief"), pk=item_id, workspace=request.workspace)


@require_permission("view_research")
@require_POST
def item_action(request, workspace_id, item_id, action):
    _require_manage(request)
    item = _item(request, item_id)
    try:
        if action == "idea":
            services.to_idea(item, request.user)
            messages.success(request, "Saved to the idea bank.")
        elif action == "plan":
            try:
                target = date.fromisoformat(request.POST.get("target_date", ""))
            except ValueError:
                target = timezone.localdate() + timedelta(days=7)
            plan = services.to_plan(item, request.user, target, draft_caption=request.POST.get("draft_caption") == "1")
            messages.success(request, "Planned as a draft post. It goes through review like any other post.")
            if item.warnings:
                messages.warning(request, " ".join(item.warnings[-3:]))
            return redirect("planning:post", workspace_id=workspace_id, post_id=plan.post_id)
        elif action == "dismiss":
            services.dismiss(item, request.user)
            messages.success(request, "Dismissed.")
        else:
            raise services.ResearchError("Unknown action.")
    except services.ResearchError as exc:
        messages.error(request, str(exc))
    return redirect("research:brief", workspace_id=workspace_id, brief_id=item.brief_id)


# ── Profile ──────────────────────────────────────────────────────────────────


class ProfileForm(forms.ModelForm):
    class Meta:
        model = ResearchProfile
        fields = ["industry", "location", "language", "audience", "competitors", "reference_accounts", "keywords",
                  "hashtags", "topics_to_avoid"]  # fmt: skip
        widgets = {f: forms.Textarea(attrs={"rows": 3}) for f in
                   ("audience", "competitors", "reference_accounts", "keywords", "hashtags", "topics_to_avoid")}  # fmt: skip

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


@require_permission("view_research")
@require_http_methods(["GET", "POST"])
def profile(request, workspace_id):
    obj = services.profile_for(request.workspace)
    form = ProfileForm(instance=obj)
    if request.method == "POST":
        _require_manage(request)
        form = ProfileForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            record("research.profile", "Research profile updated.", workspace=request.workspace)
            messages.success(request, "Research profile saved.")
            return redirect("research:profile", workspace_id=workspace_id)
    return render(request, "research/profile.html", {"workspace": request.workspace, "form": form,
                                                     "can_manage": _can_manage(request)})  # fmt: skip


# ── Swipe file ───────────────────────────────────────────────────────────────


class SwipeForm(forms.ModelForm):
    class Meta:
        model = SwipeItem
        fields = ["title", "kind", "whose", "url", "platform", "account", "source_name", "observed_on", "format", "hook",
                  "metric_note", "expires_on", "notes"]  # fmt: skip
        widgets = {
            "observed_on": forms.DateInput(attrs={"type": "date"}),
            "expires_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"whose": "From", "observed_on": "Seen on", "expires_on": "Likely over by", "metric_note": "What it showed",
                  "source_name": "Found on", "url": "Link"}  # fmt: skip

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["platform"] = forms.ChoiceField(
            choices=[
                ("", "—"),
                ("instagram", "Instagram"),
                ("facebook", "Facebook"),
                ("tiktok", "TikTok"),
                ("other", "Other"),
            ],
            required=False,
        )
        self.fields["format"] = forms.ChoiceField(
            choices=[
                ("", "—"),
                ("reel", "Reel"),
                ("carousel", "Carousel"),
                ("photo", "Photo"),
                ("video", "Video"),
                ("story", "Story"),
                ("live", "Live"),
            ],  # fmt: skip
            required=False,
        )
        _style(self)


@require_permission("view_research")
@in_local_time
@require_http_methods(["GET", "POST"])
def swipe(request, workspace_id):
    ws = request.workspace
    form = SwipeForm(initial={"observed_on": timezone.localdate()})
    if request.method == "POST":
        _require_manage(request)
        form = SwipeForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.workspace, item.saved_by = ws, request.user
            item.save()
            messages.success(request, "Saved to the swipe file.")
            return redirect("research:swipe", workspace_id=workspace_id)
    kind = request.GET.get("kind", "")
    items = SwipeItem.objects.filter(workspace=ws)
    if kind in SwipeItem.Kind.values:
        items = items.filter(kind=kind)
    return render(
        request,
        "research/swipe.html",
        {
            "workspace": ws,
            "form": form,
            "items": items[:200],
            "kind": kind,
            "kinds": SwipeItem.Kind.choices,
            "can_manage": _can_manage(request),
            "creative_center": sources.CREATIVE_CENTER_URL,
        },  # fmt: skip
    )


@require_permission("view_research")
@require_POST
def swipe_delete(request, workspace_id, item_id):
    _require_manage(request)
    get_object_or_404(SwipeItem, pk=item_id, workspace=request.workspace).delete()
    messages.success(request, "Removed from the swipe file.")
    return redirect("research:swipe", workspace_id=workspace_id)
