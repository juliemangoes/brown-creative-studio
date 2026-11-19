"""A client is a BrightBean Workspace. These models add what the studio knows about it.

Every model links to the workspace (a one-to-one profile, or
``WorkspaceScopedModel``), so client data is scoped the same way as posts and
media, and every query in this app filters on the workspace explicitly.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.managers import WorkspaceScopedManager, WorkspaceScopedModel


def _currency_choices():
    return [(code, code) for code in settings.BILLING_CURRENCIES]


class ClientProfile(models.Model):
    class Stage(models.TextChoices):
        LEAD = "lead", "Lead"
        PROPOSAL_SENT = "proposal_sent", "Proposal sent"
        ONBOARDING = "onboarding", "Onboarding"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        FORMER = "former", "Former"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.OneToOneField("workspaces.Workspace", on_delete=models.CASCADE, related_name="client_profile")
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.LEAD, db_index=True)
    stage_changed_at = models.DateTimeField(default=timezone.now)
    legal_name = models.CharField(max_length=255, blank=True, default="")
    industry = models.CharField(max_length=120, blank=True, default="")
    website = models.URLField(blank=True, default="")
    address = models.TextField(blank=True, default="", help_text="Billing address, as it should appear on invoices.")
    tax_id = models.CharField(
        max_length=60, blank=True, default="", help_text="Tax or business registration number, if any."
    )
    city = models.CharField(max_length=120, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="", help_text="ISO 3166-1 alpha-2, e.g. BZ")
    portal_language = models.CharField(max_length=5, choices=settings.INTERFACE_LANGUAGES, default="en")
    currency = models.CharField(max_length=3, default="BZD")
    started_on = models.DateField(null=True, blank=True)
    lead_source = models.CharField(max_length=120, blank=True, default="")
    # Never shown in the portal.
    internal_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "clients_profile"

    def __str__(self):
        return f"{self.workspace.name} ({self.get_stage_display()})"


class Contact(WorkspaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    title = models.CharField(max_length=120, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    is_primary = models.BooleanField(default=False)
    is_billing = models.BooleanField(default=False)
    is_approver = models.BooleanField(default=False)
    # Their portal account, once invited.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="client_contacts"
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "clients_contact"
        ordering = ["-is_primary", "name"]

    def __str__(self):
        return self.name


class ServiceLine(WorkspaceScopedModel):
    class Kind(models.TextChoices):
        SOCIAL_MEDIA = "social_media", "Social media management"
        BRANDING = "branding", "Branding"
        BRAND_STRATEGY = "brand_strategy", "Brand strategy"

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ENDED = "ended", "Ended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    started_on = models.DateField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clients_service_line"
        unique_together = [("workspace", "kind")]
        ordering = ["kind"]

    def __str__(self):
        return f"{self.workspace.name}: {self.get_kind_display()} ({self.get_status_display()})"


class Retainer(WorkspaceScopedModel):
    """What the client's plan includes each month, to compare with what was delivered."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, default="Monthly retainer")
    monthly_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="BZD")
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)
    posts_per_month = models.PositiveSmallIntegerField(default=0)
    reels_per_month = models.PositiveSmallIntegerField(default=0)
    stories_per_month = models.PositiveSmallIntegerField(default=0)
    shoot_days_per_month = models.PositiveSmallIntegerField(default=0)
    revision_rounds = models.PositiveSmallIntegerField(default=2)
    scope_notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "clients_retainer"
        ordering = ["-is_active", "-starts_on"]

    def __str__(self):
        return f"{self.workspace.name}: {self.name}"

    @classmethod
    def current_for(cls, workspace):
        today = timezone.localdate()
        return (
            cls.objects.for_workspace(workspace.id)
            .filter(is_active=True, starts_on__lte=today)
            .filter(models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=today))
            .order_by("-starts_on")
            .first()
        )
