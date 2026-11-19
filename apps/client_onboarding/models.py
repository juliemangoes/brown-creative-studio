"""Client onboarding: templates per service line, and one guided run per client.

BrightBean's ``apps.onboarding`` covers account connection and a setup checklist;
this app is the client's whole start with the studio. Step 8 reuses its
``ConnectionLink`` for connecting social accounts.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.managers import WorkspaceScopedModel


class StepKey(models.TextChoices):
    WELCOME = "welcome", "Welcome and timeline"
    PROPOSAL = "proposal", "Proposal"
    CONTRACT = "contract", "Contract"
    DEPOSIT = "deposit", "Deposit or first invoice"
    DETAILS = "details", "Business and billing details"
    QUESTIONNAIRE = "questionnaire", "Questionnaire"
    UPLOADS = "uploads", "Logos, fonts, photos and past materials"
    ACCOUNTS = "accounts", "Connect social accounts"
    KICKOFF = "kickoff", "Book the kickoff meeting"


STEP_ORDER = [k for k, _ in StepKey.choices]
# Confirmed by the studio until billing and e-signature connect them (Phase 5).
STUDIO_STEPS = {StepKey.PROPOSAL, StepKey.CONTRACT, StepKey.DEPOSIT}


class OnboardingTemplate(models.Model):
    """What onboarding asks for one service line. Several are merged when a client buys several services.

    ``questions``: ``[{"key", "label", "help", "kind": "text"|"long"|"list"|"choice", "choices": [], "required", "maps_to"}]``
    where ``maps_to`` names a ``BrandProfile`` field the answer fills (e.g. ``"voice"``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="onboarding_templates"
    )
    name = models.CharField(max_length=120)
    service_kind = models.CharField(
        max_length=30, blank=True, default="", help_text="ServiceLine kind; blank applies to every client."
    )
    welcome_message = models.TextField(blank=True, default="")
    timeline = models.JSONField(default=list, blank=True)  # [{"label", "when"}]
    steps = models.JSONField(default=list, blank=True)  # step keys this service needs
    questions = models.JSONField(default=list, blank=True)
    reminder_days = models.JSONField(default=list, blank=True)  # days after the invitation
    builtin_key = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "client_onboarding_template"
        ordering = ["name"]

    def __str__(self):
        return self.name


class OnboardingRun(WorkspaceScopedModel):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)
    services = models.JSONField(default=list, blank=True)
    welcome_message = models.TextField(blank=True, default="")
    timeline = models.JSONField(default=list, blank=True)
    questions = models.JSONField(default=list, blank=True)
    reminder_days = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_client_activity_at = models.DateTimeField(null=True, blank=True)
    next_reminder_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    kickoff_slots = models.JSONField(default=list, blank=True)  # ISO datetimes the studio offers
    kickoff_url = models.URLField(
        blank=True, default="", help_text="A booking page, if the studio uses one instead of slots."
    )
    kickoff_at = models.DateTimeField(null=True, blank=True)
    kickoff_booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    result = models.JSONField(default=dict, blank=True)  # what completion created: project ids, brand profile

    class Meta:
        db_table = "client_onboarding_run"
        ordering = ["-started_at"]

    def __str__(self):
        return f"Onboarding for {self.workspace}"

    def ordered_steps(self):
        return sorted(self.steps.all(), key=lambda s: s.position)

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.IN_PROGRESS

    def progress(self) -> tuple[int, int]:
        steps = list(self.steps.all())
        return sum(1 for s in steps if s.is_finished), len(steps)


class OnboardingStep(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING_STUDIO = "waiting_studio", "With the studio"
        DONE = "done", "Done"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(OnboardingRun, on_delete=models.CASCADE, related_name="steps")
    key = models.CharField(max_length=20, choices=StepKey.choices)
    position = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO)
    data = models.JSONField(default=dict, blank=True)
    # Internal: why this step is stuck. Never shown to the client.
    blocker = models.TextField(blank=True, default="")
    # Shown to the client on studio-confirmed steps.
    client_note = models.TextField(blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "client_onboarding_step"
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["run", "key"], name="client_onboarding_step_unique")]

    def __str__(self):
        return self.get_key_display()

    @property
    def is_finished(self) -> bool:
        return self.status in (self.Status.DONE, self.Status.SKIPPED)

    @property
    def is_studio_step(self) -> bool:
        return self.key in STUDIO_STEPS


class OnboardingParticipant(models.Model):
    """A client person taking part: when they were invited, last seen, last did something, and reminders sent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(OnboardingRun, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    invited_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_action_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "client_onboarding_participant"
        constraints = [models.UniqueConstraint(fields=["run", "user"], name="client_onboarding_participant_unique")]

    @property
    def has_responded(self) -> bool:
        return self.last_action_at is not None
