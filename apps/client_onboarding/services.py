"""Starting, filling, reminding and completing a client's onboarding.

Completion creates what the brief asks for in one go: the brand profile (filled
from the answers), a project per service with its starter tasks, the first
monthly planning cycle for social media, and the kickoff as a milestone. It also
moves the client from Onboarding to Active.
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.client_onboarding.defaults import BUILTINS, LIST_TARGETS, TEXT_TARGETS
from apps.client_onboarding.models import (
    STEP_ORDER,
    STUDIO_STEPS,
    OnboardingParticipant,
    OnboardingRun,
    OnboardingStep,
    OnboardingTemplate,
    StepKey,
)
from apps.clients.models import ClientProfile, Contact, ServiceLine
from apps.members.models import OrgMembership, WorkspaceMembership

logger = logging.getLogger(__name__)
STAFF_ROLES = {r for r in WorkspaceMembership.WorkspaceRole.values if r != WorkspaceMembership.WorkspaceRole.CLIENT}


# ── Templates ────────────────────────────────────────────────────────────────


def ensure_templates(organization) -> dict[str, OnboardingTemplate]:
    templates = {}
    for key, spec in BUILTINS.items():
        template, _ = OnboardingTemplate.objects.get_or_create(
            organization=organization,
            builtin_key=key,
            defaults={
                "name": spec["name"],
                "service_kind": spec["service_kind"],
                "welcome_message": spec.get("welcome_message", ""),
                "timeline": spec.get("timeline", []),
                "steps": spec.get("steps", []),
                "questions": spec.get("questions", []),
                "reminder_days": spec.get("reminder_days", []),
            },
        )
        templates[key] = template
    return templates


def build_flow(organization, services) -> dict:
    """One flow for a client's services: base template first, then each service's additions."""
    ensure_templates(organization)
    base = OnboardingTemplate.objects.filter(organization=organization, service_kind="").order_by("created_at").first()
    chosen = [base] if base else []
    chosen += list(
        OnboardingTemplate.objects.filter(organization=organization, service_kind__in=services).order_by("created_at")
    )
    steps, questions, timeline, seen = set(), [], [], set()
    for template in chosen:
        steps.update(template.steps)
        timeline.extend(template.timeline)
        for question in template.questions:
            if question.get("key") not in seen:
                seen.add(question.get("key"))
                questions.append(question)
    return {
        "steps": [k for k in STEP_ORDER if k in steps],
        "questions": questions,
        "timeline": timeline,
        "welcome_message": base.welcome_message if base else "",
        "reminder_days": base.reminder_days if base else [2, 5, 9],
    }


# ── Portal access for contacts ───────────────────────────────────────────────


def grant_portal_access(workspace, contact: Contact, *, preset: str, actor):
    """A portal account for a contact (no password: they sign in by email link) with a client role preset.

    Refuses studio staff of this studio: one person can't be both sides of the portal.
    """
    from apps.studio.permissions import ensure_client_roles
    from apps.studio.services import drop_default_org

    user_model = get_user_model()
    email = contact.email.strip().lower()
    if not email:
        raise ValueError(f"{contact.name} has no email address.")
    org = workspace.organization
    user = user_model.objects.filter(email__iexact=email).first()
    if (
        user is not None
        and WorkspaceMembership.objects.filter(
            user=user, workspace__organization=org, workspace_role__in=STAFF_ROLES
        ).exists()
    ):
        raise ValueError(f"{email} belongs to someone on the studio team.")
    if user is None:
        user = user_model.objects.create_user(email=email, password=None, name=contact.name)
        user.set_unusable_password()  # portal access is by emailed sign-in link
        user.save(update_fields=["password"])
    OrgMembership.objects.get_or_create(
        user=user, organization=org, defaults={"org_role": OrgMembership.OrgRole.MEMBER}
    )
    drop_default_org(user, org)
    roles = ensure_client_roles(org)
    membership, created = WorkspaceMembership.objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={"workspace_role": WorkspaceMembership.WorkspaceRole.CLIENT, "custom_role": roles[preset]},
    )
    if not created and membership.workspace_role != WorkspaceMembership.WorkspaceRole.CLIENT:
        raise ValueError(f"{email} already works on this client as studio staff.")
    if contact.user_id != user.id:
        contact.user = user
        contact.save(update_fields=["user", "updated_at"])
    return user


def invite(run, user, actor) -> bool:
    from apps.studio.portal import send_portal_email

    participant, _ = OnboardingParticipant.objects.get_or_create(run=run, user=user)
    ok = send_portal_email(
        user,
        run.workspace,
        subject=f"Let's get started: {run.workspace.name} and {settings.SITE_NAME}",
        template="client_onboarding/email/invite",
        context={"run": run},
    )
    if ok:
        participant.invited_at = timezone.now()
        participant.save(update_fields=["invited_at"])
    return ok


# ── Starting ─────────────────────────────────────────────────────────────────


@transaction.atomic
def start_run(
    workspace, actor, *, services, contacts, welcome_message=None, reminder_days=None, kickoff_url=""
) -> tuple[OnboardingRun, list[str]]:
    """Create the run and its steps, give each contact portal access, and email them. Returns (run, problems)."""
    from apps.clients.services import change_stage, profile_for

    if OnboardingRun.objects.filter(workspace=workspace, status=OnboardingRun.Status.IN_PROGRESS).exists():
        raise ValueError("This client already has onboarding in progress.")
    flow = build_flow(workspace.organization, services)
    run = OnboardingRun.objects.create(
        workspace=workspace,
        services=list(services),
        welcome_message=welcome_message if welcome_message is not None else flow["welcome_message"],
        timeline=flow["timeline"],
        questions=flow["questions"],
        reminder_days=sorted({int(d) for d in (reminder_days if reminder_days is not None else flow["reminder_days"])}),
        created_by=actor,
        kickoff_url=kickoff_url,
    )
    for position, key in enumerate(flow["steps"]):
        OnboardingStep.objects.create(
            run=run,
            key=key,
            position=position,
            status=OnboardingStep.Status.WAITING_STUDIO if key in STUDIO_STEPS else OnboardingStep.Status.TODO,
        )
    schedule_next_reminder(run)
    profile = profile_for(workspace)
    if profile.stage in (ClientProfile.Stage.LEAD, ClientProfile.Stage.PROPOSAL_SENT):
        change_stage(profile, ClientProfile.Stage.ONBOARDING, actor)
    record(
        "onboarding.started",
        f"Onboarding started for {workspace.name}.",
        workspace=workspace,
        actor=actor,
        target=run,
        data={"services": list(services)},
    )

    problems = []
    for i, contact in enumerate(contacts):
        preset = "Client admin" if (contact.is_primary or i == 0) else "Client reviewer"
        try:
            user = grant_portal_access(workspace, contact, preset=preset, actor=actor)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        transaction.on_commit(functools.partial(invite, run, user, actor))
        OnboardingParticipant.objects.get_or_create(run=run, user=user)
    return run, problems


# ── Activity ─────────────────────────────────────────────────────────────────


def touch(run, user, *, action=False) -> None:
    now = timezone.now()
    participant, _ = OnboardingParticipant.objects.get_or_create(run=run, user=user)
    participant.last_seen_at = now
    fields = ["last_seen_at"]
    if action:
        participant.last_action_at = now
        fields.append("last_action_at")
        OnboardingRun.objects.filter(pk=run.pk).update(last_client_activity_at=now)
        run.last_client_activity_at = now
    participant.save(update_fields=fields)


def finish_step(
    step: OnboardingStep, user, *, status=OnboardingStep.Status.DONE, actor_is_client=True
) -> OnboardingRun | None:
    step.status = status
    step.completed_at = timezone.now()
    step.completed_by = user
    step.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
    if actor_is_client:
        touch(step.run, user, action=True)
    return maybe_complete(step.run, user)


def maybe_complete(run, actor):
    if run.is_open and all(s.is_finished for s in run.steps.all()):
        return complete_run(run, actor)
    return None


# ── Step handlers ────────────────────────────────────────────────────────────


def missing_required(run, answers) -> list[str]:
    return [
        q["label"]
        for q in run.questions
        if q.get("required") and not str(answers.get(q["key"], {}).get("value", "")).strip()
    ]


def save_answers(step, user, values: dict, *, submit: bool) -> list[str]:
    """Store answers (who and when for each). On submit, refuse while required ones are empty."""
    answers = dict(step.data.get("answers", {}))
    now = timezone.now().isoformat()
    for question in step.run.questions:
        key = question["key"]
        if key not in values:
            continue
        value = values[key].strip()[:5000]
        if answers.get(key, {}).get("value") != value:
            answers[key] = {"value": value, "by": str(user.pk), "by_name": user.name or user.email, "at": now}
    step.data = {**step.data, "answers": answers}
    if not step.is_finished:
        step.status = OnboardingStep.Status.IN_PROGRESS
    step.save(update_fields=["data", "status", "updated_at"])
    touch(step.run, user, action=True)
    if submit:
        missing = missing_required(step.run, answers)
        if missing:
            return missing
        finish_step(step, user)
    return []


def save_details(run, user, cleaned) -> None:
    """Business and billing details go straight into the client record and contacts (entered once)."""
    from apps.clients.services import profile_for

    profile = profile_for(run.workspace)
    for field in ("legal_name", "website", "address", "city", "country", "tax_id"):
        if field in cleaned:
            setattr(profile, field, cleaned[field])
    profile.save()
    for person in cleaned.get("people", []):
        contact = (
            Contact.objects.for_workspace(run.workspace.id).filter(email__iexact=person["email"]).first()
            if person["email"]
            else None
        )
        contact = contact or Contact(workspace=run.workspace)
        contact.name = person["name"] or contact.name
        contact.email = person["email"] or contact.email
        contact.title = person.get("title", contact.title)
        contact.is_billing = person.get("is_billing", False) or contact.is_billing
        contact.is_approver = person.get("is_approver", False) or contact.is_approver
        if contact.name:
            contact.save()
    record(
        "onboarding.details_saved",
        f"{user.name or user.email} updated {run.workspace.name}'s business details.",
        workspace=run.workspace,
        actor=user,
        target=profile,
    )


UPLOAD_CATEGORIES = {
    "logos": ("logo", "Logos"),
    "fonts": ("font", "Fonts"),
    "photos": ("photo", "Photos"),
    "materials": ("reference", "Past materials"),
}


def save_uploads(run, user, category, uploads) -> tuple[int, list[str]]:
    """Logos, fonts and past materials become brand files; photos go to the client's media library."""
    from django.core.exceptions import ValidationError

    from apps.brand.models import BrandAsset
    from apps.brand.services import add_asset
    from apps.studio.files import check_upload

    kind, _ = UPLOAD_CATEGORIES[category]
    added, refused = 0, []
    for upload in uploads:
        try:
            if category == "photos":
                from apps.media_library.services import create_asset

                check_upload(upload, ["image"])
                create_asset(
                    run.workspace.organization,
                    run.workspace,
                    upload,
                    user,
                    title=upload.name,
                    tags=["onboarding"],
                    source="onboarding",
                )
            else:
                add_asset(run.workspace, upload, user=user, kind=kind, source=BrandAsset.Source.CLIENT)
            added += 1
        except ValidationError as exc:
            refused.extend(exc.messages)
        except Exception:
            logger.exception("Onboarding upload failed")
            refused.append(f"{upload.name} couldn't be saved. Please try again.")
    if added:
        touch(run, user, action=True)
        step = run.steps.filter(key=StepKey.UPLOADS).first()
        if step and step.status == OnboardingStep.Status.TODO:
            step.status = OnboardingStep.Status.IN_PROGRESS
            step.save(update_fields=["status", "updated_at"])
    return added, refused


def connection_link(run, actor=None):
    """An active BrightBean connection link for this client (reused while valid)."""
    from apps.onboarding.models import ConnectionLink

    now = timezone.now()
    link = (
        ConnectionLink.objects.filter(
            workspace=run.workspace, revoked_at__isnull=True, expires_at__gt=now + timedelta(hours=1)
        )
        .order_by("-expires_at")
        .first()
    )
    return link or ConnectionLink.objects.create(
        workspace=run.workspace, created_by=actor or run.created_by, expires_at=now + timedelta(days=7)
    )


def offered_slots(run) -> list[datetime]:
    slots = []
    for value in run.kickoff_slots:
        try:
            slot = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        if timezone.is_naive(slot):
            slot = timezone.make_aware(slot)
        if slot > timezone.now():
            slots.append(slot)
    return sorted(slots)


def book_kickoff(run, user, value: str) -> bool:
    chosen = next((s for s in offered_slots(run) if s.isoformat() == value), None)
    if chosen is None:
        return False
    run.kickoff_at, run.kickoff_booked_by = chosen, user
    run.save(update_fields=["kickoff_at", "kickoff_booked_by"])
    record(
        "onboarding.kickoff_booked",
        f"Kickoff booked for {chosen:%d %b %Y %H:%M}.",
        workspace=run.workspace,
        actor=user,
        target=run,
    )
    step = run.steps.get(key=StepKey.KICKOFF)
    finish_step(step, user)
    return True


# ── Reminders ────────────────────────────────────────────────────────────────


def schedule_next_reminder(run) -> None:
    days = sorted(run.reminder_days or [])
    run.next_reminder_at = (
        run.started_at + timedelta(days=days[run.reminders_sent]) if run.reminders_sent < len(days) else None
    )
    run.save(update_fields=["next_reminder_at"])


def client_steps_left(run) -> list[OnboardingStep]:
    return [s for s in run.ordered_steps() if not s.is_finished and not s.is_studio_step]


def send_reminders(now=None) -> int:
    """Email participants of runs that are due a reminder and still have client steps left."""
    from apps.studio.portal import send_portal_email

    now = now or timezone.now()
    sent = 0
    due = OnboardingRun.objects.filter(
        status=OnboardingRun.Status.IN_PROGRESS, next_reminder_at__lte=now
    ).select_related("workspace")
    for run in due:
        left = client_steps_left(run)
        if left:
            for participant in run.participants.select_related("user").filter(user__is_active=True):
                if not WorkspaceMembership.objects.filter(
                    user=participant.user,
                    workspace=run.workspace,
                    workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
                ).exists():
                    continue
                if send_portal_email(
                    participant.user,
                    run.workspace,
                    subject=f"A reminder from {settings.SITE_NAME}: {len(left)} onboarding step{'s' if len(left) != 1 else ''} left",
                    template="client_onboarding/email/reminder",
                    context={"run": run, "left": left},
                ):
                    participant.reminders_sent += 1
                    participant.save(update_fields=["reminders_sent"])
                    sent += 1
        run.reminders_sent += 1
        run.save(update_fields=["reminders_sent"])
        schedule_next_reminder(run)
    return sent


# ── Completion ───────────────────────────────────────────────────────────────


def _fill_brand_profile(run):
    from apps.brand.services import lines, profile_for

    profile = profile_for(run.workspace)
    answers = {}
    for step in run.steps.filter(key=StepKey.QUESTIONNAIRE):
        answers.update(step.data.get("answers", {}))
    for question in run.questions:
        target, value = question.get("maps_to"), str(answers.get(question["key"], {}).get("value", "")).strip()
        if not target or not value:
            continue
        if target in TEXT_TARGETS and not getattr(profile, target):
            setattr(profile, target, value)
        elif target in LIST_TARGETS and not getattr(profile, target):
            setattr(profile, target, lines(value)[:20])
        elif target == "audiences" and not profile.audiences:
            profile.audiences = [{"name": "Customers", "description": value}]
    profile.save()
    return profile


@transaction.atomic
def complete_run(run, actor):
    """Everything onboarding leads to: brand profile, projects with starter tasks, first planning cycle, Active."""
    from apps.brand.services import link_brand_project
    from apps.clients.services import change_stage, profile_for
    from apps.projects.models import Task
    from apps.projects.services import create_from_template, ensure_builtin_templates, month_label, month_start

    run = OnboardingRun.objects.select_for_update().get(pk=run.pk)
    if not run.is_open:
        return run
    workspace = run.workspace
    brand = _fill_brand_profile(run)
    templates = ensure_builtin_templates(workspace.organization)
    owner = run.created_by
    today = timezone.localdate()
    projects = []
    services = set(run.services)
    if ServiceLine.Kind.SOCIAL_MEDIA in services:
        projects.append(
            create_from_template(
                workspace,
                templates["social_retainer"],
                name=f"{month_label(today)} social",
                starts_on=today,
                owner=owner,
                created_by=actor,
                period_start=month_start(today),
                client_summary="Your first month: content plan, shoot, approvals, posting and a report.",
            )
        )
    if ServiceLine.Kind.BRANDING in services:
        brand_project = create_from_template(
            workspace,
            templates["brand"],
            name=f"{workspace.name} brand",
            starts_on=today,
            owner=owner,
            created_by=actor,
            client_summary="Seven stages, each one signed off by you in the Brand Hub.",
        )
        link_brand_project(brand_project)
        projects.append(brand_project)
    elif ServiceLine.Kind.BRAND_STRATEGY in services:
        strategy = create_from_template(
            workspace,
            templates["strategy"],
            name=f"{workspace.name} brand strategy",
            starts_on=today,
            owner=owner,
            created_by=actor,
            client_summary="Discovery to messaging, each stage signed off by you in the Brand Hub.",
        )
        link_brand_project(strategy)
        projects.append(strategy)
    if run.kickoff_at and projects:
        Task.objects.create(
            workspace=workspace,
            project=projects[0],
            title="Kickoff meeting",
            is_milestone=True,
            starts_on=timezone.localdate(run.kickoff_at),
            due_on=timezone.localdate(run.kickoff_at),
            assignee=owner,
            created_by=actor,
            description=f"Booked by {run.kickoff_booked_by.name or run.kickoff_booked_by.email}."
            if run.kickoff_booked_by
            else "",
        )
    profile = profile_for(workspace)
    if profile.stage in (ClientProfile.Stage.LEAD, ClientProfile.Stage.PROPOSAL_SENT, ClientProfile.Stage.ONBOARDING):
        change_stage(profile, ClientProfile.Stage.ACTIVE, actor)
    run.status = OnboardingRun.Status.COMPLETED
    run.completed_at = timezone.now()
    run.next_reminder_at = None
    run.result = {"projects": [str(p.id) for p in projects], "brand_profile": str(brand.id)}
    run.save(update_fields=["status", "completed_at", "next_reminder_at", "result"])
    record(
        "onboarding.completed",
        f"{workspace.name} finished onboarding; {len(projects)} project{'s' if len(projects) != 1 else ''} created.",
        workspace=workspace,
        actor=actor,
        target=run,
        data=run.result,
    )
    return run
