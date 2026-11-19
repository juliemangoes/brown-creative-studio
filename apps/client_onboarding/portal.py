"""The client's guided onboarding in the portal. Saves as they go; anyone on their side can help.

Any client user of the workspace may take part (the studio invites the people it
expects; others on the client's side can still help). Internal fields (step
blockers, the studio's notes on answers) are never rendered here.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.client_onboarding.defaults import PLATFORM_HELP
from apps.client_onboarding.forms import DetailsForm
from apps.client_onboarding.models import OnboardingRun, OnboardingStep, StepKey
from apps.client_onboarding.services import (
    UPLOAD_CATEGORIES,
    book_kickoff,
    connection_link,
    finish_step,
    missing_required,
    offered_slots,
    save_answers,
    save_details,
    save_uploads,
    touch,
)
from apps.studio.portal import PortalNotice, PortalSection, register_notice, register_section


def _current_run(workspace):
    return (
        OnboardingRun.objects.filter(workspace=workspace)
        .exclude(status=OnboardingRun.Status.CANCELLED)
        .order_by("-started_at")
        .first()
    )


@register_section
def _section(request):
    run = _current_run(request.portal_workspace)
    if run is None:
        return None
    return PortalSection("onboarding_portal", _("Getting started"), reverse("onboarding_portal:overview"), order=10)


@register_notice
def _notice(request):
    if getattr(request.resolver_match, "namespace", "") == "onboarding_portal":
        return None
    run = _current_run(request.portal_workspace)
    if run is None or not run.is_open:
        return None
    done, total = run.progress()
    return PortalNotice(
        _("Let's finish setting up: %(done)s of %(total)s steps done.") % {"done": done, "total": total},
        reverse("onboarding_portal:overview"),
        _("Continue"),
    )


def _portal_run(request):
    run = _current_run(request.portal_workspace)
    if run is None:
        raise Http404
    return run


def portal_view(view_func):
    from apps.client_portal.decorators import portal_auth_required

    return portal_auth_required(view_func)


def _next_step(run):
    return next((s for s in run.ordered_steps() if not s.is_finished and not s.is_studio_step), None)


@portal_view
def overview(request):
    run = _portal_run(request)
    touch(run, request.user)
    steps = run.ordered_steps()
    done, total = run.progress()
    return render(
        request,
        "client_onboarding/portal/overview.html",
        {
            "workspace": request.portal_workspace,
            "run": run,
            "steps": steps,
            "done": done,
            "total": total,
            "next_step": _next_step(run),
        },
    )


def _step(run, key):
    if key not in StepKey.values:
        raise Http404
    step = run.steps.filter(key=key).first()
    if step is None:
        raise Http404
    return step


def _after(run, step):
    """Where to go after a step: the next client step, else the overview."""
    nxt = _next_step(run)
    if nxt and nxt.pk != step.pk:
        return redirect("onboarding_portal:step", key=nxt.key)
    return redirect("onboarding_portal:overview")


@portal_view
@require_http_methods(["GET", "POST"])
def step_view(request, key):
    run = _portal_run(request)
    step = _step(run, key)
    workspace = request.portal_workspace
    if request.method == "POST" and not run.is_open:
        messages.error(request, _("Onboarding is finished; nothing more to fill in."))
        return redirect("onboarding_portal:overview")
    touch(run, request.user)
    ctx = {"workspace": workspace, "run": run, "step": step, "steps": run.ordered_steps()}

    if key == StepKey.WELCOME:
        if request.method == "POST":
            finish_step(step, request.user)
            return _after(run, step)

    elif key == StepKey.DETAILS:
        from apps.clients.models import Contact
        from apps.clients.services import profile_for

        profile = profile_for(workspace)
        billing = Contact.objects.for_workspace(workspace.id).filter(is_billing=True).first()
        form = DetailsForm(
            request.POST or None,
            initial={
                "legal_name": profile.legal_name,
                "website": profile.website,
                "address": profile.address,
                "city": profile.city,
                "country": profile.country,
                "tax_id": profile.tax_id,
                "billing_name": billing.name if billing else "",
                "billing_email": billing.email if billing else "",
            },
        )
        people = list(
            Contact.objects.for_workspace(workspace.id).values("name", "email", "title", "is_billing", "is_approver")
        )
        if request.method == "POST" and form.is_valid():
            cleaned = dict(form.cleaned_data)
            extra = []
            try:
                for row in json.loads(request.POST.get("people_json") or "[]")[:20]:
                    name, email = str(row.get("name", "")).strip()[:255], str(row.get("email", "")).strip()[:254]
                    if name or email:
                        extra.append(
                            {
                                "name": name,
                                "email": email,
                                "title": str(row.get("title", ""))[:120],
                                "is_approver": bool(row.get("is_approver")),
                                "is_billing": False,
                            }
                        )
            except ValueError:
                pass
            if cleaned.get("billing_name") or cleaned.get("billing_email"):
                extra.append(
                    {
                        "name": cleaned["billing_name"],
                        "email": cleaned["billing_email"],
                        "title": "",
                        "is_billing": True,
                        "is_approver": False,
                    }
                )
            cleaned["people"] = extra
            save_details(run, request.user, cleaned)
            finish_step(step, request.user)
            messages.success(request, _("Thanks, your details are saved."))
            return _after(run, step)
        ctx.update({"form": form, "people": people})

    elif key == StepKey.QUESTIONNAIRE:
        answers = step.data.get("answers", {})
        if request.method == "POST":
            values = {q["key"]: request.POST.get(f"q_{q['key']}", "") for q in run.questions}
            missing = save_answers(step, request.user, values, submit=request.POST.get("submit") == "1")
            if request.POST.get("submit") != "1":
                messages.success(request, _("Saved. You can come back and finish any time."))
                return redirect("onboarding_portal:step", key=key)
            if missing:
                messages.error(request, _("A few answers are still needed: %(list)s") % {"list": "; ".join(missing)})
                return redirect("onboarding_portal:step", key=key)
            messages.success(request, _("Thank you, that's really helpful."))
            return _after(run, step)
        ctx["questions"] = [(q, answers.get(q["key"], {})) for q in run.questions]
        ctx["missing"] = missing_required(run, answers)

    elif key == StepKey.UPLOADS:
        from apps.brand.models import BrandAsset

        if request.method == "POST":
            finish_step(
                step,
                request.user,
                status=OnboardingStep.Status.SKIPPED if request.POST.get("none") == "1" else OnboardingStep.Status.DONE,
            )
            return _after(run, step)
        ctx["categories"] = [(k, label) for k, (_kind, label) in UPLOAD_CATEGORIES.items()]
        ctx["uploaded"] = BrandAsset.objects.filter(workspace=workspace, source=BrandAsset.Source.CLIENT).order_by(
            "-created_at"
        )[:50]

    elif key == StepKey.ACCOUNTS:
        from apps.social_accounts.models import SocialAccount

        if request.method == "POST":
            accounts = SocialAccount.objects.for_workspace(workspace.id).exists()
            finish_step(
                step, request.user, status=OnboardingStep.Status.DONE if accounts else OnboardingStep.Status.SKIPPED
            )
            return _after(run, step)
        link = connection_link(run)
        ctx.update(
            {
                "connect_url": reverse("onboarding:connection_page", kwargs={"token": link.token}),
                "accounts": SocialAccount.objects.for_workspace(workspace.id).order_by("platform"),
                "platform_help": PLATFORM_HELP,
            }
        )

    elif key == StepKey.KICKOFF:
        if request.method == "POST":
            if request.POST.get("external") == "1" and run.kickoff_url:
                finish_step(step, request.user)
                return _after(run, step)
            if book_kickoff(run, request.user, request.POST.get("slot", "")):
                messages.success(request, _("Kickoff booked. See you then!"))
                return _after(run, step)
            messages.error(request, _("That time isn't available any more. Please choose another."))
            return redirect("onboarding_portal:step", key=key)
        ctx["slots"] = offered_slots(run)

    elif step.is_studio_step:
        pass  # proposal, contract, deposit: confirmed by the studio; the page explains what happens

    return render(request, "client_onboarding/portal/step.html", ctx)


@portal_view
@require_POST
def upload(request, category):
    run = _portal_run(request)
    if category not in UPLOAD_CATEGORIES or not run.is_open:
        raise Http404
    files = request.FILES.getlist("files")[:20]
    if not files:
        messages.error(request, _("Choose at least one file."))
        return redirect("onboarding_portal:step", key=StepKey.UPLOADS)
    try:
        added, refused = save_uploads(run, request.user, category, files)
    except ValidationError as exc:
        added, refused = 0, exc.messages
    if added:
        messages.success(request, _("%(n)s file(s) uploaded. Thank you!") % {"n": added})
    for message in refused:
        messages.error(request, message)
    return redirect("onboarding_portal:step", key=StepKey.UPLOADS)
