"""Shoots: the shared calendar, a client's shoots and runway, one shoot, on-set mode, call sheets.

``view_shoots`` opens a client's shoots; ``manage_shoots`` changes them. Freelancers
(contributors) see only shoots they are crew on. The public call sheet is a
share link the studio turns on; it shows nothing internal.
"""

from __future__ import annotations

import json
from datetime import time, timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.audit.services import record
from apps.clients.forms import _style
from apps.clients.models import Contact
from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.shoots import services
from apps.shoots.callsheet import render_pdf
from apps.shoots.models import Shoot, Shot
from apps.studio.decorators import in_local_time, in_studio_time, studio_staff_required
from apps.studio.permissions import workspace_permission


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class ShootForm(forms.ModelForm):
    locations_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Locations (one per line)"
    )
    equipment_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Equipment (one per line)"
    )
    talent_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Talent (Name: role, one per line)"
    )
    run_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}), required=False, label="Run of show (time | what, one per line)"
    )
    prep_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Client preparation checklist (one per line)"
    )

    class Meta:
        model = Shoot
        fields = [
            "title",
            "status",
            "date",
            "call_time",
            "wrap_time",
            "crew",
            "client_contact",
            "wardrobe",
            "props",
            "permits",
            "release_forms",
            "client_notes",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "call_time": forms.TimeInput(attrs={"type": "time"}),
            "wrap_time": forms.TimeInput(attrs={"type": "time"}),
            "crew": forms.CheckboxSelectMultiple,
        }
        labels = {
            "client_notes": "Notes for the call sheet",
            "notes": "Internal notes (never on the call sheet)",
            "client_contact": "Client contact on site",
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.projects.forms import staff_choices

        self.fields["crew"].queryset = staff_choices(workspace)
        self.fields["crew"].label_from_instance = lambda u: u.name or u.email
        self.fields["client_contact"].queryset = Contact.objects.for_workspace(workspace.id)
        inst = self.instance
        if inst.pk:
            self.initial.update(
                {
                    "locations_text": "\n".join(inst.locations),
                    "equipment_text": "\n".join(inst.equipment),
                    "talent_text": "\n".join(
                        f"{t.get('name', '')}: {t.get('role', '')}".rstrip(": ") for t in inst.talent
                    ),
                    "run_text": "\n".join(f"{r.get('time', '')} | {r.get('item', '')}" for r in inst.run_of_show),
                    "prep_text": "\n".join(i.get("text", "") for i in inst.prep_checklist),
                }
            )
        for name in ("wardrobe", "props", "permits", "release_forms", "client_notes", "notes"):
            self.fields[name].widget.attrs["rows"] = 2
        _style(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("wrap_time") and cleaned.get("call_time") and cleaned["wrap_time"] <= cleaned["call_time"]:
            self.add_error("wrap_time", "Wrap is before the call time.")
        return cleaned

    def save(self, commit=True):
        shoot = super().save(commit=False)
        data = self.cleaned_data
        shoot.locations = _lines(data["locations_text"])
        shoot.equipment = _lines(data["equipment_text"])
        shoot.talent = [
            {"name": n.strip(), "role": r.strip()}
            for line in _lines(data["talent_text"])
            for n, _, r in [line.partition(":")]
        ]
        shoot.run_of_show = [
            {"time": t.strip(), "item": i.strip()}
            for line in _lines(data["run_text"])
            for t, _, i in [line.partition("|")]
        ]
        done = {i.get("text"): i.get("done", False) for i in (self.instance.prep_checklist or [])}
        shoot.prep_checklist = [{"text": text, "done": done.get(text, False)} for text in _lines(data["prep_text"])]
        if commit:
            shoot.save()
            self.save_m2m()
        return shoot


# ── Scope ────────────────────────────────────────────────────────────────────


def _crew_only(membership) -> bool:
    perms = membership.effective_permissions
    return membership.workspace_role == WorkspaceMembership.WorkspaceRole.CONTRIBUTOR and not perms.get(
        "manage_shoots", False
    )


def _shoots(request):
    qs = Shoot.objects.filter(workspace=request.workspace)
    if _crew_only(request.workspace_membership):
        qs = qs.filter(crew=request.user)
    return qs


def _shoot(request, shoot_id):
    return get_object_or_404(_shoots(request), pk=shoot_id)


def _can_manage(request) -> bool:
    return workspace_permission(request, "manage_shoots")


def _require_manage(request):
    if not _can_manage(request):
        raise PermissionDenied("Permission denied: manage_shoots")


# ── All clients ──────────────────────────────────────────────────────────────


@studio_staff_required
@in_studio_time
def all_shoots(request):
    memberships = (
        WorkspaceMembership.objects.filter(
            user=request.user, workspace__organization=request.org, workspace__is_archived=False
        )
        .exclude(workspace_role="client")
        .select_related("workspace", "custom_role")
    )
    visible = []
    for m in memberships:
        if not m.effective_permissions.get("view_shoots", False):
            continue
        qs = Shoot.objects.filter(workspace=m.workspace)
        if _crew_only(m):
            qs = qs.filter(crew=request.user)
        visible.append(qs)
    today = timezone.localdate()
    shoots = []
    for qs in visible:
        shoots.extend(
            qs.filter(date__gte=today - timedelta(days=14)).select_related("workspace").prefetch_related("crew")
        )
    shoots.sort(key=lambda s: (s.date, s.call_time))
    return render(request, "shoots/all.html", {"rows": [(s, services.conflicts(s)) for s in shoots], "today": today})


# ── One client ───────────────────────────────────────────────────────────────


@require_permission("view_shoots")
@in_local_time
def client_shoots(request, workspace_id):
    ws = request.workspace
    today = timezone.localdate()
    shoots = list(_shoots(request).prefetch_related("crew", "shots"))
    return render(
        request,
        "shoots/client.html",
        {
            "workspace": ws,
            "upcoming": [s for s in shoots if s.date >= today and s.status != Shoot.Status.CANCELLED],
            "past": [s for s in shoots if s.date < today or s.status == Shoot.Status.CANCELLED][::-1][:20],
            "runway": services.runway(ws, today),
            "form": ShootForm(
                workspace=ws,
                initial={"date": today + timedelta(days=7), "call_time": time(9, 0), "title": "Content day"},
            )
            if _can_manage(request)
            else None,
            "can_manage": _can_manage(request),
        },
    )


@require_permission("view_shoots")
@require_POST
def shoot_create(request, workspace_id):
    _require_manage(request)
    form = ShootForm(request.POST, workspace=request.workspace)
    if not form.is_valid():
        messages.error(request, "Check the shoot: " + " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("shoots:list", workspace_id=workspace_id)
    shoot = form.save(commit=False)
    shoot.workspace, shoot.created_by = request.workspace, request.user
    shoot.save()
    form.save_m2m()
    record(
        "shoot.created",
        f"Shoot {shoot.title} on {shoot.date:%d %b} created",
        workspace=request.workspace,
        actor=request.user,
        target=shoot,
    )
    messages.success(request, f"{shoot.title} on {shoot.date:%a %-d %b} created. Add shots from planned posts next.")
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@in_local_time
@require_http_methods(["GET", "POST"])
def shoot_detail(request, workspace_id, shoot_id):
    shoot = _shoot(request, shoot_id)
    form = ShootForm(request.POST or None, instance=shoot, workspace=request.workspace)
    if request.method == "POST":
        _require_manage(request)
        if form.is_valid():
            form.save()
            messages.success(request, "Shoot saved.")
            return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)
        messages.error(request, "Check the highlighted fields.")
    shots = list(shoot.shots.select_related("post", "post__content_plan").prefetch_related("media"))
    from apps.media_library.models import MediaAsset

    unmatched = MediaAsset.objects.filter(
        workspace=request.workspace, rights__shoot=shoot, shots__isnull=True
    ).order_by("-created_at")[:40]
    return render(
        request,
        "shoots/detail.html",
        {
            "workspace": request.workspace,
            "shoot": shoot,
            "form": form,
            "shots": shots,
            "conflicts": services.conflicts(shoot),
            "candidates": services.candidate_posts(shoot) if _can_manage(request) else [],
            "unmatched": unmatched,
            "can_manage": _can_manage(request),
            "share_url": request.build_absolute_uri(
                reverse("shoots_public:call_sheet", kwargs={"token": shoot.share_token})
            ),
            "captured": sum(1 for s in shots if s.status == Shot.Status.CAPTURED),
            "statuses": Shot.Status.choices,
        },
    )


@require_permission("view_shoots")
@require_POST
def shot_add(request, workspace_id, shoot_id):
    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    description = request.POST.get("description", "").strip()
    if description:
        services.add_shot(
            shoot,
            description=description,
            location=request.POST.get("location", ""),
            priority=request.POST.get("priority", "must")
            if request.POST.get("priority") in Shot.Priority.values
            else "must",
            user=request.user,
        )
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def shots_from_posts(request, workspace_id, shoot_id):
    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    ids = request.POST.getlist("post_ids")
    posts = list(services.candidate_posts(shoot).filter(id__in=ids))
    made = services.shots_from_posts(shoot, posts, request.user)
    messages.success(request, f"{len(made)} shot{'s' if len(made) != 1 else ''} added, each tied to its post.")
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def shot_update(request, workspace_id, shoot_id, shot_id):
    shoot = _shoot(request, shoot_id)
    shot = get_object_or_404(shoot.shots, pk=shot_id)
    # Crew can tick shots off; managers can also edit and remove them.
    if request.POST.get("delete") == "1":
        _require_manage(request)
        shot.delete()
        return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)
    if "status" in request.POST:
        services.mark_shot(shot, request.POST["status"], request.user)
    if "notes" in request.POST:
        shot.notes = request.POST["notes"][:4000]
        shot.save(update_fields=["notes", "updated_at"])
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def upload(request, workspace_id, shoot_id):
    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    files = request.FILES.getlist("files")[:50]
    matched, unmatched, refused = services.upload_and_match(shoot, request.user, files)
    if matched or unmatched:
        messages.success(
            request,
            f"{len(matched) + len(unmatched)} file(s) added to the media library; {len(matched)} matched to shots by code.",
        )
    for message in refused:
        messages.error(request, message)
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def match(request, workspace_id, shoot_id):
    from apps.media_library.models import MediaAsset

    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    asset = get_object_or_404(MediaAsset.objects.filter(workspace=request.workspace), pk=request.POST.get("asset"))
    shot = get_object_or_404(shoot.shots, pk=request.POST.get("shot"))
    services.attach_to_shot(shot, asset, request.user)
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def followups(request, workspace_id, shoot_id):
    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    tasks = services.create_followups(shoot, request.user)
    if shoot.status != Shoot.Status.DONE:
        shoot.status = Shoot.Status.DONE
        shoot.save(update_fields=["status", "updated_at"])
    messages.success(
        request,
        f"Shoot marked done. {len(tasks)} follow-up task{'s' if len(tasks) != 1 else ''} created for missing shots."
        if tasks
        else "Shoot marked done. Every must-have shot was captured.",
    )
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def share(request, workspace_id, shoot_id):
    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    action = request.POST.get("action")
    if action == "on":
        shoot.share_enabled = True
    elif action == "off":
        shoot.share_enabled = False
    elif action == "new":
        from apps.shoots.models import _token

        shoot.share_token = _token()
    shoot.save(update_fields=["share_enabled", "share_token", "updated_at"])
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
@require_POST
def send_prep(request, workspace_id, shoot_id):
    """Email the client contact the preparation checklist and the call sheet link (and turn the link on)."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from apps.common.mail import send_or_raise, transactional

    _require_manage(request)
    shoot = _shoot(request, shoot_id)
    contact = shoot.client_contact
    if contact is None or not contact.email:
        messages.error(request, "Choose a client contact with an email address first.")
        return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)
    shoot.share_enabled = True
    url = request.build_absolute_uri(reverse("shoots_public:call_sheet", kwargs={"token": shoot.share_token}))
    ctx = {"shoot": shoot, "contact": contact, "url": url, "pdf_url": url + "pdf/", "site_name": settings.SITE_NAME}
    msg = EmailMultiAlternatives(
        subject=f"{shoot.workspace.name}: getting ready for {shoot.title} on {shoot.date:%d %b}",
        body=render_to_string("shoots/email/prep.txt", ctx),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[contact.email],
        headers=transactional(),
    )
    msg.attach_alternative(render_to_string("shoots/email/prep.html", ctx), "text/html")
    try:
        send_or_raise(msg)
    except Exception:  # noqa: BLE001 - shown to the user
        messages.error(request, "The email couldn't be sent. The call sheet link still works; copy it from this page.")
    else:
        shoot.prep_sent_at = timezone.now()
        messages.success(request, f"Preparation checklist and call sheet sent to {contact.name}.")
    shoot.save(update_fields=["share_enabled", "prep_sent_at", "updated_at"])
    record(
        "shoot.prep_sent",
        f"Preparation checklist for {shoot.title} sent to {contact.email}",
        workspace=request.workspace,
        actor=request.user,
        target=shoot,
    )
    return redirect("shoots:detail", workspace_id=workspace_id, shoot_id=shoot.id)


@require_permission("view_shoots")
def call_sheet(request, workspace_id, shoot_id):
    shoot = _shoot(request, shoot_id)
    if request.GET.get("format") == "pdf":
        return _pdf_response(shoot)
    return render(
        request,
        "shoots/call_sheet.html",
        {"shoot": shoot, "public": False, "shots": shoot.shots.order_by("position", "code")},
    )


def _pdf_response(shoot):
    contact_line = f"{settings.SITE_NAME}" + (f" · {settings.SUPPORT_EMAIL}" if settings.SUPPORT_EMAIL else "")
    response = HttpResponse(
        render_pdf(shoot, studio_name=settings.SITE_NAME, contact_line=contact_line), content_type="application/pdf"
    )
    response["Content-Disposition"] = f'attachment; filename="call-sheet-{shoot.date:%Y-%m-%d}.pdf"'
    return response


# ── Public call sheet (share link) ───────────────────────────────────────────


def _public(token):
    shoot = (
        Shoot.objects.filter(share_token=token, share_enabled=True)
        .select_related("workspace", "client_contact")
        .first()
    )
    if shoot is None:
        raise Http404
    return shoot


@require_GET
def public_call_sheet(request, token):
    shoot = _public(token)
    response = render(
        request,
        "shoots/call_sheet.html",
        {"shoot": shoot, "public": True, "shots": shoot.shots.order_by("position", "code")},
    )
    response["X-Robots-Tag"] = "noindex"
    return response


@require_GET
def public_call_sheet_pdf(request, token):
    return _pdf_response(_public(token))


# ── On-set mode (works offline) ──────────────────────────────────────────────


@require_permission("view_shoots")
@in_local_time
def on_set(request, workspace_id, shoot_id):
    shoot = _shoot(request, shoot_id)
    shots = [
        {
            "id": str(s.id),
            "code": s.code,
            "description": s.description,
            "status": s.status,
            "notes": s.notes,
            "priority": s.priority,
            "location": s.location,
            "unplanned": s.is_unplanned,
            "post": (s.post.title if s.post_id else ""),
        }
        for s in shoot.shots.select_related("post").order_by("position", "code")
    ]
    response = render(
        request,
        "shoots/on_set.html",
        {
            "workspace": request.workspace,
            "shoot": shoot,
            "data": {
                "shoot": str(shoot.id),
                "title": shoot.title,
                "shots": shots,
                "sync": reverse("shoots:sync", kwargs={"workspace_id": workspace_id, "shoot_id": shoot.id}),
                "loaded": timezone.now().isoformat(),
            },
        },
    )
    response["Cache-Control"] = "no-cache"
    return response


@require_permission("view_shoots")
@require_POST
def sync(request, workspace_id, shoot_id):
    shoot = _shoot(request, shoot_id)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    ops = payload.get("ops") if isinstance(payload, dict) else None
    if not isinstance(ops, list):
        return JsonResponse({"error": "ops required"}, status=400)
    result = services.apply_ops(shoot, request.user, ops)
    shots = [{"id": str(s.id), "status": s.status, "notes": s.notes} for s in shoot.shots.all()]
    return JsonResponse({**result, "shots": shots})


@require_GET
def service_worker(request):
    """Served from /shoots/ so it can control on-set pages (scope /shoots/)."""
    from django.contrib.staticfiles import finders

    path = finders.find("shoots/onset-sw.js")
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    response = HttpResponse(body, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/shoots/"
    response["Cache-Control"] = "no-cache"
    return response
