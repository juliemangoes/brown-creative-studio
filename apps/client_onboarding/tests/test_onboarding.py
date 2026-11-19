"""Client onboarding end to end, plus scoping, reminders and templates.

The Phase 3 goal: a new client can finish onboarding and a project is created automatically.
"""

import io
import re
from datetime import timedelta

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.audit.models import AuditEvent
from apps.brand.models import BrandAsset, BrandStage
from apps.client_onboarding.models import OnboardingRun, OnboardingStep
from apps.client_onboarding.services import grant_portal_access, send_reminders, start_run
from apps.clients.models import ClientProfile, Contact
from apps.clients.services import create_client
from apps.projects.models import Project, Task
from apps.studio import scanning
from apps.studio.tests.helpers import make_studio

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (107, 79, 58)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def studio(db):
    return make_studio()


@pytest.fixture
def new_client(studio):
    ws = create_client(
        studio["org"],
        studio["owner"],
        name="Vista Realty",
        stage=ClientProfile.Stage.LEAD,
        services=["social_media", "branding"],
        contact_name="Marco Ruiz",
        contact_email="marco@vista.test",
        industry="Real estate",
    )
    return ws


def _ws(name, ws, **kw):
    return reverse(name, kwargs={"workspace_id": ws.id, **kw})


def _portal_path(message):
    return re.search(r"https?://[^/]+(/portal/[^/\s]+/)", message.body).group(1)


def test_new_client_finishes_onboarding_and_projects_are_created(
    client, studio, new_client, django_capture_on_commit_callbacks
):
    ws = new_client
    contact = Contact.objects.for_workspace(ws.id).get()

    # 1. The studio starts onboarding; Marco gets a portal account and an email.
    client.force_login(studio["owner"])
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            _ws("client_onboarding:start", ws),
            {"services": ["social_media", "branding"], "contacts": [contact.id], "reminder_days": "2, 5"},
        )
    assert response.status_code == 302
    run = OnboardingRun.objects.get(workspace=ws)
    assert [s.key for s in run.ordered_steps()] == [
        "welcome",
        "proposal",
        "contract",
        "deposit",
        "details",
        "questionnaire",
        "uploads",
        "accounts",
        "kickoff",
    ]
    assert ClientProfile.objects.get(workspace=ws).stage == "onboarding"
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["marco@vista.test"]
    marco = Contact.objects.get(pk=contact.pk).user
    assert marco is not None and not marco.has_usable_password()

    # The studio offers kickoff times.
    slot = (timezone.localtime() + timedelta(days=3)).replace(hour=10, minute=30, second=0, microsecond=0)
    client.post(_ws("client_onboarding:kickoff", ws), {"slots": slot.strftime("%Y-%m-%d %H:%M")})
    client.logout()

    # 2. Marco signs in with the emailed link (BrightBean's magic link flow) and lands in the portal.
    path = _portal_path(mail.outbox[0])
    assert client.get(path).status_code == 200
    response = client.post(path)
    assert response.status_code == 302
    marco.tos_accepted_at = timezone.now()
    marco.save()
    overview = client.get(reverse("onboarding_portal:overview"))
    assert overview.status_code == 200 and "Getting started" in overview.content.decode()

    # 3. He works through the steps.
    step_url = lambda key: reverse("onboarding_portal:step", kwargs={"key": key})  # noqa: E731
    client.post(step_url("welcome"))
    client.post(
        step_url("details"),
        {
            "legal_name": "Vista Realty Ltd.",
            "address": "1 Coastal Rd",
            "city": "Belmopan",
            "country": "bz",
            "billing_name": "Rita Ruiz",
            "billing_email": "rita@vista.test",
            "people_json": '[{"name": "Sam Lee", "email": "sam@vista.test", "title": "Marketing", "is_approver": true}]',
        },
    )
    profile = ClientProfile.objects.get(workspace=ws)
    assert (profile.legal_name, profile.country) == ("Vista Realty Ltd.", "BZ")
    assert Contact.objects.for_workspace(ws.id).filter(email="rita@vista.test", is_billing=True).exists()
    assert Contact.objects.for_workspace(ws.id).filter(email="sam@vista.test", is_approver=True).exists()

    answers = {
        "q_goals": "More viewing requests",
        "q_audience": "Young families moving to Belmopan",
        "q_tone": "Warm and friendly",
        "q_competitors": "Belize Homes\nCayo Realty",
        "q_story": "Founded by two sisters in 2015.",
        "q_personality": "Trustworthy\nWarm",
    }
    response = client.post(
        step_url("questionnaire"), {**{k: v for k, v in answers.items() if k != "q_story"}, "submit": "1"}
    )
    assert OnboardingStep.objects.get(run=run, key="questionnaire").status == "in_progress"  # story is required
    client.post(step_url("questionnaire"), {**answers, "submit": "1"})
    assert OnboardingStep.objects.get(run=run, key="questionnaire").status == "done"

    client.post(
        reverse("onboarding_portal:upload", kwargs={"category": "logos"}),
        {"files": [SimpleUploadedFile("vista-logo.svg", SVG)]},
    )
    client.post(
        reverse("onboarding_portal:upload", kwargs={"category": "photos"}),
        {"files": [SimpleUploadedFile("office.png", png_bytes())]},
    )
    client.post(
        reverse("onboarding_portal:upload", kwargs={"category": "materials"}),
        {"files": [SimpleUploadedFile("old.pdf", b"%PDF-1.4 " + scanning.EICAR)]},
    )
    assert list(BrandAsset.objects.filter(workspace=ws).values_list("original_name", "source")) == [
        ("vista-logo.svg", "client")
    ]
    from apps.media_library.models import MediaAsset

    assert MediaAsset.objects.filter(workspace=ws, source="onboarding").count() == 1
    client.post(step_url("uploads"))
    client.post(step_url("accounts"))  # no accounts yet: skipped
    assert OnboardingStep.objects.get(run=run, key="accounts").status == "skipped"
    client.post(step_url("kickoff"), {"slot": timezone.localtime(slot).isoformat()})
    run.refresh_from_db()
    assert run.kickoff_at is not None and run.status == "in_progress"  # studio steps still open

    # 4. The studio confirms proposal, contract and deposit; onboarding completes itself.
    client.force_login(studio["owner"])
    for step in run.steps.filter(key__in=["proposal", "contract", "deposit"]):
        client.post(
            _ws("client_onboarding:step_action", ws, step_id=step.id), {"action": "done", "client_note": "Thank you!"}
        )
    run.refresh_from_db()
    assert run.status == "completed"

    # 5. What completion created.
    projects = Project.objects.filter(workspace=ws)
    assert {p.kind for p in projects} == {"social_retainer", "brand"}
    social = projects.get(kind="social_retainer")
    assert social.tasks.filter(template_key="plan").exists() and social.period_start == timezone.localdate().replace(
        day=1
    )
    assert Task.objects.filter(project__workspace=ws, title="Kickoff meeting", is_milestone=True).exists()
    assert BrandStage.objects.filter(workspace=ws).count() == 7
    brand = ws.brand_profile
    assert brand.audiences == [{"name": "Customers", "description": "Young families moving to Belmopan"}]
    assert brand.voice == "Warm and friendly" and brand.competitors == ["Belize Homes", "Cayo Realty"]
    assert brand.purpose == "Founded by two sisters in 2015." and brand.personality == ["Trustworthy", "Warm"]
    assert ClientProfile.objects.get(workspace=ws).stage == "active"
    assert AuditEvent.objects.filter(workspace=ws, verb="onboarding.completed").exists()


# ── Scoping ──────────────────────────────────────────────────────────────────


@pytest.fixture
def running(studio, django_capture_on_commit_callbacks):
    contact = Contact.objects.create(
        workspace=studio["restaurant"], name="Ana", email="ana@casamaya.test", is_primary=True
    )
    with django_capture_on_commit_callbacks(execute=True):
        run, problems = start_run(studio["restaurant"], studio["owner"], services=["social_media"], contacts=[contact])
    return run


def portal_login(client, user, workspace):
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(workspace.id)
    session.save()


@pytest.mark.parametrize("name", ["client_onboarding:run"])
def test_studio_onboarding_pages_are_scoped(client, studio, running, name):
    client.force_login(studio["editor"])
    assert client.get(_ws(name, studio["skincare"])).status_code == 403
    client.force_login(studio["restaurant_client"])
    assert client.get(_ws(name, studio["restaurant"])).status_code == 403
    client.force_login(studio["owner"])
    assert client.get(_ws(name, studio["restaurant"])).status_code == 200


def test_step_ids_from_another_client_are_404(client, studio, running):
    step = running.steps.get(key="proposal")
    client.force_login(studio["owner"])
    assert (
        client.post(
            _ws("client_onboarding:step_action", studio["skincare"], step_id=step.id), {"action": "done"}
        ).status_code
        == 404
    )
    step.refresh_from_db()
    assert step.status == "waiting_studio"


def test_only_managers_change_onboarding(client, studio, running):
    step = running.steps.get(key="proposal")
    client.force_login(studio["editor"])  # editor: no manage_client_profile
    assert client.get(_ws("client_onboarding:run", studio["restaurant"])).status_code == 200
    assert (
        client.post(
            _ws("client_onboarding:step_action", studio["restaurant"], step_id=step.id), {"action": "done"}
        ).status_code
        == 403
    )
    assert client.post(_ws("client_onboarding:cancel", studio["restaurant"])).status_code == 403


def test_portal_shows_only_own_run_and_no_internal_notes(client, studio, running):
    step = running.steps.get(key="proposal")
    step.blocker, step.client_note = "Waiting on the owner's cousin", "We'll send it Monday"
    step.save()
    portal_login(client, studio["skincare_client"], studio["skincare"])
    assert client.get(reverse("onboarding_portal:overview")).status_code == 404  # skincare has no onboarding
    assert client.post(reverse("onboarding_portal:step", kwargs={"key": "welcome"})).status_code == 404
    portal_login(client, studio["restaurant_client"], studio["restaurant"])
    body = client.get(reverse("onboarding_portal:step", kwargs={"key": "proposal"})).content.decode()
    assert "send it Monday" in body and "cousin" not in body
    dashboard = client.get(reverse("client_portal:dashboard")).content.decode()
    assert "Getting started" in dashboard and "steps done" in dashboard


def test_client_users_cannot_complete_studio_steps(client, studio, running):
    portal_login(client, studio["restaurant_client"], studio["restaurant"])
    client.post(reverse("onboarding_portal:step", kwargs={"key": "proposal"}))
    assert running.steps.get(key="proposal").status == "waiting_studio"


def test_portal_access_refuses_studio_staff(studio):
    contact = Contact.objects.create(workspace=studio["restaurant"], name="Me", email="editor@studio.test")
    with pytest.raises(ValueError, match="studio team"):
        grant_portal_access(studio["restaurant"], contact, preset="Client admin", actor=studio["owner"])


# ── Reminders ────────────────────────────────────────────────────────────────


def test_reminders_follow_the_schedule(studio, running):
    mail.outbox.clear()
    assert running.next_reminder_at == running.started_at + timedelta(days=2)
    assert send_reminders(now=running.started_at + timedelta(days=1)) == 0
    assert send_reminders(now=running.started_at + timedelta(days=2, minutes=1)) == 1
    assert "steps left" in mail.outbox[0].subject and "Book the kickoff" in mail.outbox[0].body
    running.refresh_from_db()
    assert running.reminders_sent == 1 and running.next_reminder_at == running.started_at + timedelta(days=5)
    assert send_reminders(now=running.started_at + timedelta(days=2, hours=2)) == 0  # not twice


def test_no_reminder_when_only_studio_steps_are_left(studio, running):
    running.steps.exclude(key__in=["proposal", "contract", "deposit"]).update(status="done")
    mail.outbox.clear()
    assert send_reminders(now=running.started_at + timedelta(days=3)) == 0
    running.refresh_from_db()
    assert running.reminders_sent == 1  # the slot passed, nothing sent


def test_cancelled_runs_get_no_reminders(client, studio, running):
    client.force_login(studio["owner"])
    client.post(_ws("client_onboarding:cancel", studio["restaurant"]))
    mail.outbox.clear()
    assert send_reminders(now=timezone.now() + timedelta(days=30)) == 0


# ── Templates ────────────────────────────────────────────────────────────────


def test_template_editing_needs_manage_templates(client, studio):
    from apps.client_onboarding.services import ensure_templates

    template = ensure_templates(studio["org"])["social_media"]
    client.force_login(studio["manager"])
    assert (
        client.get(reverse("client_onboarding:template_edit", kwargs={"template_id": template.id})).status_code == 403
    )
    client.force_login(studio["owner"])
    response = client.post(
        reverse("client_onboarding:template_edit", kwargs={"template_id": template.id}),
        {
            "name": "Social",
            "steps": ["accounts"],
            "timeline": "First posts | Week 4",
            "reminder_days": "3",
            "questions_json": '[{"label": "Your best-selling dish?", "kind": "text", "maps_to": "nonsense"}]',
        },
    )
    assert response.status_code == 302
    template.refresh_from_db()
    assert template.questions[0]["label"] == "Your best-selling dish?" and template.questions[0]["maps_to"] == ""
    assert template.timeline == [{"label": "First posts", "when": "Week 4"}]
