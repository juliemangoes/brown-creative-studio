"""Studio side of onboarding: start a run, follow progress, unblock steps, remind, edit templates."""

from __future__ import annotations

from datetime import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.audit.services import record
from apps.client_onboarding.forms import ReminderForm, StartForm
from apps.client_onboarding.models import OnboardingRun, OnboardingStep, OnboardingTemplate, StepKey
from apps.client_onboarding.services import (
    client_steps_left,
    ensure_templates,
    finish_step,
    grant_portal_access,
    invite,
    maybe_complete,
    offered_slots,
    schedule_next_reminder,
    start_run,
)
from apps.clients.models import Contact, ServiceLine
from apps.clients.views import _context as client_context
from apps.members.decorators import require_permission
from apps.studio.decorators import in_local_time, require_studio_permission, studio_staff_required
from apps.studio.permissions import workspace_permission


def _run(workspace):
    return (
        OnboardingRun.objects.filter(workspace=workspace)
        .exclude(status=OnboardingRun.Status.CANCELLED)
        .order_by("-started_at")
        .first()
    )


def _require_manage(request):
    if not workspace_permission(request, "manage_client_profile"):
        raise PermissionDenied("Permission denied: manage_client_profile")


def _open_run(request):
    run = _run(request.workspace)
    if run is None or not run.is_open:
        messages.error(request, "There's no onboarding in progress for this client.")
        return None
    return run


@require_permission("view_client_profile")
@in_local_time
def run_detail(request, workspace_id):
    ctx = client_context(request, "onboarding")
    ws = request.workspace
    run = _run(ws)
    can_manage = workspace_permission(request, "manage_client_profile")
    ctx["can_manage"] = can_manage
    if run is None:
        active = list(
            ServiceLine.objects.for_workspace(ws.id)
            .exclude(status=ServiceLine.Status.ENDED)
            .values_list("kind", flat=True)
        )
        flow_defaults = {}
        if can_manage:
            from apps.client_onboarding.services import build_flow

            flow_defaults = build_flow(request.org, active)
        ctx.update(
            {
                "run": None,
                "form": StartForm(
                    workspace=ws,
                    initial={
                        "services": active,
                        "contacts": list(
                            Contact.objects.for_workspace(ws.id).exclude(email="").filter(is_primary=True)
                        ),
                        "welcome_message": flow_defaults.get("welcome_message", ""),
                        "reminder_days": ", ".join(str(d) for d in flow_defaults.get("reminder_days", [])),
                    },
                )
                if can_manage
                else None,
            }
        )
        return render(request, "client_onboarding/run.html", ctx)

    steps = run.ordered_steps()
    done, total = run.progress()
    answers = {}
    for step in steps:
        if step.key == StepKey.QUESTIONNAIRE:
            answers = step.data.get("answers", {})
    participants = list(run.participants.select_related("user").order_by("user__name"))
    invited_contacts = {p.user_id for p in participants}
    ctx.update(
        {
            "run": run,
            "steps": steps,
            "done": done,
            "total": total,
            "percent": round(100 * done / total) if total else 0,
            "participants": participants,
            "questions": [(q, answers.get(q["key"])) for q in run.questions],
            "client_left": client_steps_left(run),
            "studio_left": [s for s in steps if s.is_studio_step and not s.is_finished],
            "reminder_form": ReminderForm(initial={"reminder_days": ", ".join(str(d) for d in run.reminder_days)}),
            "slots": offered_slots(run),
            "uninvited": Contact.objects.for_workspace(ws.id).exclude(email="").exclude(user_id__in=invited_contacts),
            "projects": _result_projects(run),
        }
    )
    return render(request, "client_onboarding/run.html", ctx)


def _result_projects(run):
    from apps.projects.models import Project

    ids = run.result.get("projects", []) if run.result else []
    return list(Project.objects.filter(workspace=run.workspace, id__in=ids))


@require_permission("view_client_profile")
@require_POST
def run_start(request, workspace_id):
    _require_manage(request)
    form = StartForm(request.POST, workspace=request.workspace)
    if not form.is_valid():
        messages.error(
            request, "Check the onboarding details: " + " ".join(e for errs in form.errors.values() for e in errs)
        )
        return redirect("client_onboarding:run", workspace_id=workspace_id)
    data = form.cleaned_data
    try:
        run, problems = start_run(
            request.workspace,
            request.user,
            services=data["services"],
            contacts=list(data["contacts"]),
            welcome_message=data["welcome_message"],
            reminder_days=data["reminder_days"] or None,
            kickoff_url=data["kickoff_url"],
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("client_onboarding:run", workspace_id=workspace_id)
    invited = run.participants.count()
    messages.success(
        request,
        f"Onboarding started. {invited} {'person was' if invited == 1 else 'people were'} emailed a link to begin.",
    )
    for problem in problems:
        messages.error(request, problem)
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def step_action(request, workspace_id, step_id):
    _require_manage(request)
    step = get_object_or_404(
        OnboardingStep.objects.filter(run__workspace=request.workspace, run__status=OnboardingRun.Status.IN_PROGRESS),
        pk=step_id,
    )
    action = request.POST.get("action")
    if action == "done":
        step.client_note = request.POST.get("client_note", step.client_note).strip()
        step.save(update_fields=["client_note", "updated_at"])
        finish_step(step, request.user, actor_is_client=False)
        record(
            "onboarding.step_confirmed",
            f"{step.get_key_display()} marked done by the studio.",
            workspace=request.workspace,
            actor=request.user,
            target=step.run,
        )
    elif action == "skip":
        finish_step(step, request.user, status=OnboardingStep.Status.SKIPPED, actor_is_client=False)
        record(
            "onboarding.step_skipped",
            f"{step.get_key_display()} skipped.",
            workspace=request.workspace,
            actor=request.user,
            target=step.run,
        )
    elif action == "reopen":
        step.status = OnboardingStep.Status.WAITING_STUDIO if step.is_studio_step else OnboardingStep.Status.TODO
        step.completed_at = None
        step.save(update_fields=["status", "completed_at", "updated_at"])
    elif action == "blocker":
        step.blocker = request.POST.get("blocker", "").strip()[:1000]
        step.client_note = request.POST.get("client_note", step.client_note).strip()[:1000]
        step.save(update_fields=["blocker", "client_note", "updated_at"])
    run = OnboardingRun.objects.get(pk=step.run_id)
    if run.status == OnboardingRun.Status.COMPLETED:
        messages.success(request, "Onboarding is complete. The brand profile and projects have been created.")
    else:
        messages.success(request, f"{step.get_key_display()} updated.")
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def reminders_save(request, workspace_id):
    _require_manage(request)
    run = _open_run(request)
    if run:
        form = ReminderForm(request.POST)
        if form.is_valid():
            run.reminder_days = form.cleaned_data["reminder_days"]
            run.reminders_sent = sum(
                1 for d in run.reminder_days if run.started_at.timestamp() + d * 86400 <= timezone.now().timestamp()
            )
            run.save(update_fields=["reminder_days", "reminders_sent"])
            schedule_next_reminder(run)
            messages.success(request, "Reminder schedule saved.")
        else:
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def remind_now(request, workspace_id):
    _require_manage(request)
    run = _open_run(request)
    if run:
        sent = 0
        for participant in run.participants.select_related("user"):
            if invite(run, participant.user, request.user):
                sent += 1
        messages.success(request, f"Sent a fresh link to {sent} {'person' if sent == 1 else 'people'}.")
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def invite_contact(request, workspace_id):
    _require_manage(request)
    run = _open_run(request)
    if run:
        contact = get_object_or_404(Contact.objects.for_workspace(request.workspace.id), pk=request.POST.get("contact"))
        try:
            user = grant_portal_access(
                request.workspace,
                contact,
                preset=request.POST.get("preset", "Client reviewer")
                if request.POST.get("preset") in ("Client admin", "Client reviewer", "Client billing contact")
                else "Client reviewer",
                actor=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            invite(run, user, request.user)
            messages.success(request, f"{contact.name} was invited.")
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def kickoff_slots(request, workspace_id):
    _require_manage(request)
    run = _open_run(request)
    if run:
        slots, bad = [], []
        for line in request.POST.get("slots", "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                slot = timezone.make_aware(datetime.fromisoformat(line.replace(" ", "T")))
            except ValueError:
                bad.append(line)
                continue
            slots.append(slot.isoformat())
        run.kickoff_slots = slots
        run.kickoff_url = request.POST.get("kickoff_url", "").strip()[:200]
        run.save(update_fields=["kickoff_slots", "kickoff_url"])
        if bad:
            messages.error(request, f"Couldn't read: {', '.join(bad)}. Use the form 2026-12-03 10:30.")
        else:
            messages.success(request, f"{len(slots)} kickoff time{'s' if len(slots) != 1 else ''} offered.")
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def run_cancel(request, workspace_id):
    _require_manage(request)
    run = _open_run(request)
    if run:
        run.status = OnboardingRun.Status.CANCELLED
        run.next_reminder_at = None
        run.save(update_fields=["status", "next_reminder_at"])
        record(
            "onboarding.cancelled",
            "Onboarding was cancelled.",
            workspace=request.workspace,
            actor=request.user,
            target=run,
        )
        messages.success(request, "Onboarding cancelled. Reminders stop now.")
    return redirect("client_onboarding:run", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def run_complete(request, workspace_id):
    """Finish now: remaining steps are skipped (the studio confirms it has what it needs)."""
    _require_manage(request)
    run = _open_run(request)
    if run:
        for step in run.steps.exclude(status__in=[OnboardingStep.Status.DONE, OnboardingStep.Status.SKIPPED]):
            step.status = OnboardingStep.Status.SKIPPED
            step.completed_by, step.completed_at = request.user, timezone.now()
            step.save(update_fields=["status", "completed_by", "completed_at", "updated_at"])
        maybe_complete(run, request.user)
        messages.success(request, "Onboarding completed. The brand profile and projects have been created.")
    return redirect("client_onboarding:run", workspace_id=workspace_id)


# ── Templates ────────────────────────────────────────────────────────────────


@studio_staff_required
def template_list(request):
    from apps.studio.permissions import has_studio_permission

    ensure_templates(request.org)
    return render(
        request,
        "client_onboarding/templates.html",
        {
            "templates": OnboardingTemplate.objects.filter(organization=request.org).order_by("service_kind", "name"),
            "can_edit": has_studio_permission(request.org_membership, "manage_templates"),
            "service_names": dict(ServiceLine.Kind.choices),
        },
    )


@require_studio_permission("manage_templates")
@require_http_methods(["GET", "POST"])
def template_edit(request, template_id):
    import json

    from apps.client_onboarding.forms import parse_days
    from apps.client_onboarding.models import STEP_ORDER

    template = get_object_or_404(OnboardingTemplate, pk=template_id, organization=request.org)
    errors = []
    if request.method == "POST":
        template.name = request.POST.get("name", template.name).strip()[:120] or template.name
        template.welcome_message = request.POST.get("welcome_message", "").strip()[:4000]
        template.steps = [k for k in STEP_ORDER if k in request.POST.getlist("steps")]
        template.timeline = [
            {"label": label.strip(), "when": when.strip()}
            for line in request.POST.get("timeline", "").splitlines()
            if line.strip()
            for label, _, when in [line.partition("|")]
        ]
        try:
            template.reminder_days = parse_days(request.POST.get("reminder_days", ""))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            errors.append(" ".join(getattr(exc, "messages", [str(exc)])))
        try:
            questions = json.loads(request.POST.get("questions_json") or "[]")
            template.questions = _clean_questions(questions)
        except ValueError:
            errors.append("The questions couldn't be read.")
        if not errors:
            template.save()
            messages.success(
                request, f"“{template.name}” saved. New onboardings use it; ones in progress keep their questions."
            )
            return redirect("client_onboarding:templates")
    return render(
        request,
        "client_onboarding/template_edit.html",
        {"template": template, "errors": errors, "step_choices": StepKey.choices, "questions": template.questions},
    )


def _clean_questions(rows):
    import re

    from apps.client_onboarding.defaults import LIST_TARGETS, TEXT_TARGETS

    out, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        label = str(row.get("label", "")).strip()[:300]
        if not label:
            continue
        key = re.sub(r"[^a-z0-9_]", "_", str(row.get("key") or label.lower())[:40]).strip("_") or f"q{len(out)}"
        while key in seen:
            key += "_2"
        seen.add(key)
        kind = row.get("kind") if row.get("kind") in ("text", "long", "list", "choice") else "long"
        maps_to = row.get("maps_to") if row.get("maps_to") in TEXT_TARGETS | LIST_TARGETS | {"audiences"} else ""
        out.append(
            {
                "key": key,
                "label": label,
                "kind": kind,
                "help": str(row.get("help", "")).strip()[:300],
                "required": bool(row.get("required")),
                "choices": [str(c).strip()[:120] for c in row.get("choices", []) if str(c).strip()][:12]
                if kind == "choice"
                else [],
                "maps_to": maps_to,
            }
        )
    return out
