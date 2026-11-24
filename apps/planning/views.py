"""The 13-week plan per client and across clients, production details, ideas, campaigns and key dates."""

from __future__ import annotations

from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.audit.services import record
from apps.clients.forms import _style
from apps.composer.models import ContentCategory, Idea, Post
from apps.members.decorators import require_permission
from apps.planning.models import Campaign, ContentPlan, KeyDate
from apps.planning.services import (
    ensure_holidays,
    plan_for,
    plan_idea,
    plan_weeks,
    planning_warnings,
    quick_plan,
    set_stage,
    week_start,
)
from apps.studio.decorators import in_local_time, in_studio_time, studio_staff_required
from apps.studio.permissions import workspace_permission
from apps.studio.today import staff_memberships


def _can_plan(request) -> bool:
    return workspace_permission(request, "create_posts")


def _require_plan(request):
    if not _can_plan(request):
        raise PermissionDenied("Permission denied: create_posts")


def _start(request):
    try:
        return date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        return timezone.localdate()


class QuickPlanForm(forms.Form):
    title = forms.CharField(max_length=255)
    target_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Date")
    format = forms.ChoiceField(choices=[("", "Not decided")] + list(ContentPlan.Format.choices), required=False)
    pillar = forms.ModelChoiceField(queryset=ContentCategory.objects.none(), required=False, label="Pillar")
    campaign = forms.ModelChoiceField(queryset=Campaign.objects.none(), required=False)
    stage = forms.ChoiceField(
        choices=ContentPlan.Stage.choices, initial=ContentPlan.Stage.PLANNED, label="Production stage", required=False
    )

    def clean_stage(self):
        return self.cleaned_data.get("stage") or ContentPlan.Stage.PLANNED

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pillar"].queryset = ContentCategory.objects.for_workspace(workspace.id)
        self.fields["campaign"].queryset = Campaign.objects.for_workspace(workspace.id)
        _style(self)


class PlanForm(forms.ModelForm):
    pillar = forms.ModelChoiceField(queryset=ContentCategory.objects.none(), required=False, label="Pillar")

    class Meta:
        model = ContentPlan
        fields = [
            "stage",
            "objective",
            "format",
            "hook",
            "script",
            "call_to_action",
            "hashtags",
            "owner",
            "campaign",
            "target_date",
            "draft_due",
            "review_due",
        ]
        widgets = {f: forms.DateInput(attrs={"type": "date"}) for f in ("target_date", "draft_due", "review_due")}
        labels = {
            "call_to_action": "Call to action",
            "draft_due": "Draft due",
            "review_due": "Ready for review by",
            "target_date": "Target date",
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.projects.forms import staff_choices

        self.fields["pillar"].queryset = ContentCategory.objects.for_workspace(workspace.id)
        self.fields["pillar"].initial = self.instance.post.category_id if self.instance.pk else None
        self.fields["campaign"].queryset = Campaign.objects.for_workspace(workspace.id)
        self.fields["owner"].queryset = staff_choices(workspace)
        self.fields["owner"].label_from_instance = lambda u: u.name or u.email
        self.fields["script"].widget.attrs["rows"] = 5
        self.fields["hashtags"].widget.attrs["rows"] = 2
        _style(self)


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "objective", "starts_on", "ends_on", "color", "notes"]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("starts_on") and cleaned.get("ends_on") and cleaned["ends_on"] < cleaned["starts_on"]:
            self.add_error("ends_on", "The end is before the start.")
        return cleaned


# ── Plan views ───────────────────────────────────────────────────────────────


@require_permission("view_plan")
@in_local_time
def board(request, workspace_id):
    ws = request.workspace
    start = _start(request)
    weeks = plan_weeks([ws], start)
    ideas = (
        Idea.objects.for_workspace(ws.id)
        .filter(post__isnull=True)
        .exclude(status=Idea.Status.DONE)
        .order_by("-created_at")[:30]
    )
    upcoming = [i for wk in weeks for i in wk.items if i.day >= timezone.localdate()][:200]
    pillar_mix, format_mix = {}, {}
    for item in upcoming:
        if item.day <= timezone.localdate() + timedelta(days=30):
            if item.post.category_id:
                pillar_mix.setdefault(item.post.category.name, [item.post.category.color, 0])[1] += 1
            if item.plan and item.plan.format:
                format_mix[item.plan.get_format_display()] = format_mix.get(item.plan.get_format_display(), 0) + 1
    return render(
        request,
        "planning/board.html",
        {
            "workspace": ws,
            "weeks": weeks,
            "warnings": planning_warnings(ws, weeks),
            "ideas": ideas,
            "pillar_mix": sorted(pillar_mix.items(), key=lambda kv: -kv[1][1]),
            "format_mix": sorted(format_mix.items(), key=lambda kv: -kv[1]),
            "mix_total": sum(1 for i in upcoming if i.day <= timezone.localdate() + timedelta(days=30)),
            "can_plan": _can_plan(request),
            "form": QuickPlanForm(workspace=ws, initial={"target_date": timezone.localdate() + timedelta(days=7)}),
            "prev": (week_start(start) - timedelta(days=28)).isoformat(),
            "next": (week_start(start) + timedelta(days=28)).isoformat(),
            "today": timezone.localdate(),
        },
    )


@studio_staff_required
@in_studio_time
def all_clients(request):
    memberships = staff_memberships(request.user, request.org)
    workspaces = [m.workspace for m in memberships if m.effective_permissions.get("view_plan", False)]
    selected = request.GET.get("client", "")
    shown = [ws for ws in workspaces if not selected or str(ws.id) == selected]
    start = _start(request)
    return render(
        request,
        "planning/all.html",
        {
            "weeks": plan_weeks(shown, start) if shown else [],
            "clients": workspaces,
            "selected": selected,
            "prev": (week_start(start) - timedelta(days=28)).isoformat(),
            "next": (week_start(start) + timedelta(days=28)).isoformat(),
            "today": timezone.localdate(),
        },
    )


@require_permission("view_plan")
@require_permission("create_posts")
@require_POST
def quick_add(request, workspace_id):
    _require_plan(request)
    form = QuickPlanForm(request.POST, workspace=request.workspace)
    if not form.is_valid():
        messages.error(request, "Give the post a title and a date.")
        return redirect("planning:board", workspace_id=workspace_id)
    data = form.cleaned_data
    plan = quick_plan(
        request.workspace,
        request.user,
        title=data["title"],
        target_date=data["target_date"],
        format=data["format"],
        pillar=data["pillar"],
        campaign=data["campaign"],
        stage=data["stage"],
    )
    messages.success(request, f"“{plan.post.title}” planned for {data['target_date']:%a %-d %b}.")
    return redirect("planning:board", workspace_id=workspace_id)


@require_permission("view_plan")
@require_permission("create_posts")
@require_POST
def idea_to_plan(request, workspace_id, idea_id):
    _require_plan(request)
    idea = get_object_or_404(Idea.objects.for_workspace(request.workspace.id), pk=idea_id)
    try:
        target = date.fromisoformat(request.POST.get("target_date", ""))
    except ValueError:
        target = timezone.localdate() + timedelta(days=7)
    plan = plan_idea(
        idea,
        request.user,
        target_date=target,
        format=request.POST.get("format", "") if request.POST.get("format") in ContentPlan.Format.values else "",
    )
    messages.success(request, f"“{idea.title}” is on the calendar for {target:%a %-d %b}.")
    return redirect("planning:post", workspace_id=workspace_id, post_id=plan.post_id)


def _post(request, post_id):
    return get_object_or_404(Post.objects.filter(workspace=request.workspace), pk=post_id)


@require_permission("view_plan")
@in_local_time
@require_http_methods(["GET", "POST"])
def post_detail(request, workspace_id, post_id):
    post = _post(request, post_id)
    plan = plan_for(post)
    form = PlanForm(request.POST or None, instance=plan, workspace=request.workspace)
    if request.method == "POST":
        _require_plan(request)
        if form.is_valid():
            previous_stage = ContentPlan.objects.get(pk=plan.pk).stage
            new_stage = form.cleaned_data["stage"]
            plan = form.save(commit=False)
            plan.stage = previous_stage
            plan.save()
            set_stage(plan, new_stage, request.user)
            pillar = form.cleaned_data.get("pillar")
            if (pillar.pk if pillar else None) != post.category_id:
                post.category = pillar
                post.save(update_fields=["category", "updated_at"])
            if plan.target_date and not post.scheduled_at:
                from apps.planning.services import _target_datetime

                post.proposed_publish_at = _target_datetime(request.workspace, plan.target_date)
                post.save(update_fields=["proposed_publish_at", "updated_at"])
            messages.success(request, "Production details saved.")
            return redirect("planning:post", workspace_id=workspace_id, post_id=post.id)
        messages.error(request, "Check the highlighted fields.")
    from apps.planning.services import combined_label

    shots = list(post.shots.select_related("shoot").order_by("shoot__date")) if hasattr(post, "shots") else []
    return render(
        request,
        "planning/post.html",
        {
            "workspace": request.workspace,
            "post": post,
            "plan": plan,
            "form": form,
            "labels": [
                (pp, combined_label(pp, post=post, plan=plan))
                for pp in post.platform_posts.select_related("social_account")
            ],
            "shots": shots,
            "media": list(
                post.media_attachments.select_related("media_asset", "media_asset__rights").order_by("position")
            ),
            "can_plan": _can_plan(request),
            "stages": ContentPlan.Stage.choices,
        },
    )


@require_permission("view_plan")
@require_permission("create_posts")
@require_POST
def stage(request, workspace_id, post_id):
    _require_plan(request)
    post = _post(request, post_id)
    set_stage(plan_for(post), request.POST.get("stage", ""), request.user)
    return redirect("planning:post", workspace_id=workspace_id, post_id=post.id)


# ── Campaigns and key dates ──────────────────────────────────────────────────


@require_permission("view_plan")
@in_local_time
@require_http_methods(["GET", "POST"])
def campaigns(request, workspace_id, campaign_id=None):
    ws = request.workspace
    campaign = (
        get_object_or_404(Campaign.objects.for_workspace(ws.id), pk=campaign_id)
        if campaign_id
        else Campaign(workspace=ws)
    )
    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST":
        _require_plan(request)
        if request.POST.get("delete") == "1" and campaign.pk:
            name = campaign.name
            campaign.delete()
            messages.success(request, f"{name} removed.")
            return redirect("planning:campaigns", workspace_id=workspace_id)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.workspace = ws
            saved.created_by = saved.created_by or request.user
            saved.save()
            record(
                "planning.campaign_saved",
                f"Campaign {saved.name} saved.",
                workspace=ws,
                actor=request.user,
                target=saved,
            )
            messages.success(request, f"{saved.name} saved.")
            return redirect("planning:campaigns", workspace_id=workspace_id)
    return render(
        request,
        "planning/campaigns.html",
        {
            "workspace": ws,
            "campaigns": Campaign.objects.for_workspace(ws.id),
            "form": form,
            "editing": campaign if campaign.pk else None,
            "can_plan": _can_plan(request),
        },
    )


@require_permission("view_plan")
@in_local_time
@require_http_methods(["GET", "POST"])
def key_dates(request, workspace_id):
    ws = request.workspace
    if request.method == "POST":
        _require_plan(request)
        if request.POST.get("delete"):
            KeyDate.objects.filter(pk=request.POST["delete"], workspace=ws).delete()
        elif request.POST.get("holidays") == "1":
            year = timezone.localdate().year
            made = ensure_holidays(ws.organization, [year, year + 1])
            messages.success(
                request,
                f"{made} public holidays added for every client." if made else "The public holidays are already there.",
            )
        else:
            try:
                day = date.fromisoformat(request.POST.get("date", ""))
            except ValueError:
                messages.error(request, "Choose a date.")
                return redirect("planning:key_dates", workspace_id=workspace_id)
            title = request.POST.get("title", "").strip()[:160]
            if title:
                KeyDate.objects.get_or_create(
                    organization=ws.organization,
                    workspace=ws,
                    date=day,
                    title=title,
                    defaults={
                        "kind": request.POST.get("kind")
                        if request.POST.get("kind") in KeyDate.Kind.values
                        else KeyDate.Kind.CLIENT
                    },
                )
        return redirect("planning:key_dates", workspace_id=workspace_id)
    from django.db.models import Q

    today = timezone.localdate()
    dates = KeyDate.objects.filter(organization=ws.organization, date__gte=today - timedelta(days=7)).filter(
        Q(workspace__isnull=True) | Q(workspace=ws)
    )
    return render(
        request,
        "planning/key_dates.html",
        {"workspace": ws, "dates": dates[:120], "kinds": KeyDate.Kind.choices, "can_plan": _can_plan(request)},
    )
