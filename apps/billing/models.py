"""Proposals, contracts, invoices, credit notes, payments and tax documents for each client.

Money is Decimal throughout, rounded to cents only for display and stored totals.
Every client-owned record links to a Workspace; studio-wide settings and the
service catalog belong to the Organization.
"""

from __future__ import annotations

import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.managers import WorkspaceScopedModel
from apps.studio.files import PrivateFile

CENT = Decimal("0.01")
ZERO = Decimal("0")


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _token() -> str:
    return secrets.token_urlsafe(32)


def _default_currency() -> str:
    return (getattr(settings, "BILLING_CURRENCIES", None) or ["BZD"])[0]


# ── Studio-wide ──────────────────────────────────────────────────────────────


class BillingSettings(models.Model):
    """The studio's invoice details, numbering, payment instructions and reminder schedule."""

    organization = models.OneToOneField(
        "organizations.Organization", on_delete=models.CASCADE, related_name="billing_settings"
    )
    legal_name = models.CharField(max_length=255, blank=True, default="")
    address = models.TextField(blank=True, default="")
    tax_id = models.CharField(max_length=60, blank=True, default="", help_text="As it must appear on invoices.")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    base_currency = models.CharField(max_length=3, default=_default_currency)
    bank_details = models.TextField(
        blank=True, default="", help_text="Bank, account name and number, SWIFT: shown on invoices and in the portal."
    )
    payment_instructions = models.TextField(blank=True, default="")
    invoice_prefix = models.CharField(max_length=12, default="INV-")
    next_invoice_number = models.PositiveIntegerField(default=1)
    credit_note_prefix = models.CharField(max_length=12, default="CN-")
    next_credit_note_number = models.PositiveIntegerField(default=1)
    proposal_prefix = models.CharField(max_length=12, default="P-")
    next_proposal_number = models.PositiveIntegerField(default=1)
    payment_terms_days = models.PositiveSmallIntegerField(default=14)
    tax_name = models.CharField(max_length=40, blank=True, default="", help_text="E.g. GST. Leave blank for no tax.")
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=ZERO, help_text="Percent, e.g. 12.5")
    reminder_days = models.JSONField(
        default=list, blank=True, help_text="Days relative to the due date: -3 is three days before, 7 a week after."
    )
    footer_note = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    DEFAULT_REMINDERS = [-3, 0, 7, 14]

    def __str__(self):
        return self.legal_name or self.organization.name


class CatalogItem(models.Model):
    """A service the studio sells, reused on proposals and invoices."""

    class Unit(models.TextChoices):
        MONTH = "month", _("per month")
        PROJECT = "project", _("per project")
        DAY = "day", _("per day")
        ITEM = "item", _("each")
        HOUR = "hour", _("per hour")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    service_kind = models.CharField(max_length=20, blank=True, default="", help_text="Service line it belongs to.")
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.PROJECT)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=3, default=_default_currency)
    recurring = models.BooleanField(default=False, help_text="Billed every month (a retainer).")
    # What a monthly package includes: it becomes the client's retainer scope when a proposal is accepted.
    posts_per_month = models.PositiveSmallIntegerField(default=0)
    reels_per_month = models.PositiveSmallIntegerField(default=0)
    stories_per_month = models.PositiveSmallIntegerField(default=0)
    shoot_days_per_month = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DocumentTemplate(models.Model):
    """Reusable proposal introductions and contract terms. ``{{ client }}``-style fields are filled in."""

    class Kind(models.TextChoices):
        PROPOSAL = "proposal", _("Proposal")
        CONTRACT = "contract", _("Contract")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=160)
    body = models.TextField()
    builtin_key = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name


# ── Proposals and contracts ──────────────────────────────────────────────────


class Proposal(WorkspaceScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")
        VIEWED = "viewed", _("Viewed")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=30, blank=True, default="")
    title = models.CharField(max_length=200)
    introduction = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    currency = models.CharField(max_length=3, default=_default_currency)
    valid_until = models.DateField(null=True, blank=True)
    deposit_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("50"), help_text="Of the one-off total, invoiced on acceptance."
    )
    services = models.JSONField(default=list, blank=True, help_text="Service lines this proposal starts.")
    contract_template = models.ForeignKey(
        DocumentTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    start_onboarding = models.BooleanField(default=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    accepted_name = models.CharField(max_length=160, blank=True, default="")
    decline_reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.number or 'Draft'} · {self.title}"

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.SENT, self.Status.VIEWED)

    def one_off_total(self) -> Decimal:
        return money(sum((line.total for line in self.lines.all() if not line.recurring), ZERO))

    def monthly_total(self) -> Decimal:
        return money(sum((line.total for line in self.lines.all() if line.recurring), ZERO))

    def package(self) -> dict:
        """What the monthly lines include, from their catalog packages (times the quantity)."""
        totals = {"posts": 0, "reels": 0, "stories": 0, "shoot_days": 0}
        for line in self.lines.all():
            item = line.catalog_item
            if line.recurring and item is not None:
                qty = int(line.quantity)
                totals["posts"] += item.posts_per_month * qty
                totals["reels"] += item.reels_per_month * qty
                totals["stories"] += item.stories_per_month * qty
                totals["shoot_days"] += item.shoot_days_per_month * qty
        return totals

    def deposit_amount(self) -> Decimal:
        return money(self.one_off_total() * self.deposit_percent / 100)


class ProposalLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="lines")
    catalog_item = models.ForeignKey(CatalogItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    recurring = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    @property
    def total(self) -> Decimal:
        gross = Decimal(self.quantity) * Decimal(self.unit_price)
        return money(gross - gross * Decimal(self.discount_percent) / 100)


class Contract(WorkspaceScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent for signature")
        VIEWED = "viewed", _("Viewed")
        SIGNED = "signed", _("Signed")
        VOID = "void", _("Void")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(Proposal, on_delete=models.SET_NULL, null=True, blank=True, related_name="contracts")
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Frozen when sent; what the client signs.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signer_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    signer_name = models.CharField(max_length=160, blank=True, default="")
    signer_email = models.EmailField(blank=True, default="")
    signer_ip = models.GenericIPAddressField(null=True, blank=True)
    signer_user_agent = models.CharField(max_length=300, blank=True, default="")
    document_hash = models.CharField(max_length=64, blank=True, default="", help_text="SHA-256 of the signed text.")
    provider = models.CharField(max_length=20, default="builtin")
    provider_reference = models.CharField(max_length=120, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.SENT, self.Status.VIEWED)


# ── Invoices ─────────────────────────────────────────────────────────────────


class Invoice(WorkspaceScopedModel):
    class Kind(models.TextChoices):
        INVOICE = "invoice", _("Invoice")
        CREDIT_NOTE = "credit_note", _("Credit note")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")
        VIEWED = "viewed", _("Viewed")
        PARTLY_PAID = "partly_paid", _("Partly paid")
        PAID = "paid", _("Paid")
        OVERDUE = "overdue", _("Overdue")
        VOID = "void", _("Void")

    class Purpose(models.TextChoices):
        DEPOSIT = "deposit", _("Deposit")
        MILESTONE = "milestone", _("Milestone")
        ONE_TIME = "one_time", _("One-time")
        RETAINER = "retainer", _("Monthly retainer")
        EXTRA = "extra", _("Extras")

    OPEN_STATUSES = (Status.SENT, Status.VIEWED, Status.PARTLY_PAID, Status.OVERDUE)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.INVOICE)
    number = models.CharField(max_length=30, blank=True, default="", db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    purpose = models.CharField(max_length=10, choices=Purpose.choices, default=Purpose.ONE_TIME)
    title = models.CharField(max_length=200, blank=True, default="")
    credits = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="credit_notes",
        help_text="For a credit note: the invoice it reduces.",
    )  # fmt: skip
    proposal = models.ForeignKey(Proposal, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    retainer = models.ForeignKey(
        "clients.Retainer", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    currency = models.CharField(max_length=3, default=_default_currency)
    base_currency = models.CharField(max_length=3, default=_default_currency)
    exchange_rate = models.DecimalField(
        max_digits=14, decimal_places=6, default=Decimal("1"), help_text="Units of the base currency per 1 of this one."
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    notes = models.TextField(blank=True, default="", help_text="Shown to the client.")
    internal_notes = models.TextField(blank=True, default="", help_text="Never shown to the client.")
    bill_to_name = models.CharField(max_length=255, blank=True, default="")
    bill_to_address = models.TextField(blank=True, default="")
    bill_to_tax_id = models.CharField(max_length=60, blank=True, default="")
    # Stored totals (recomputed by services.recalculate) so lists and the finance view can query them.
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    amount_credited = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    share_token = models.CharField(max_length=64, unique=True, default=_token)
    sent_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(kind="credit_note") | models.Q(credits__isnull=False),
                name="billing_credit_note_has_invoice",
            )
        ]

    def __str__(self):
        return self.number or f"Draft {self.get_kind_display().lower()}"

    @property
    def is_credit_note(self) -> bool:
        return self.kind == self.Kind.CREDIT_NOTE

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def total_in_base(self) -> Decimal:
        return money(Decimal(self.total) * Decimal(self.exchange_rate))

    @property
    def balance_in_base(self) -> Decimal:
        return money(Decimal(self.balance) * Decimal(self.exchange_rate))

    @property
    def days_overdue(self) -> int:
        if self.status != self.Status.OVERDUE or not self.due_date:
            return 0
        return (timezone.localdate() - self.due_date).days


class InvoiceLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    catalog_item = models.ForeignKey(CatalogItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=300)
    service_kind = models.CharField(max_length=20, blank=True, default="")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_name = models.CharField(max_length=40, blank=True, default="")
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=ZERO)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    @property
    def gross(self) -> Decimal:
        return Decimal(self.quantity) * Decimal(self.unit_price)

    @property
    def discount(self) -> Decimal:
        return self.gross * Decimal(self.discount_percent) / 100

    @property
    def net(self) -> Decimal:
        return money(self.gross - self.discount)


class Payment(WorkspaceScopedModel):
    class Method(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", _("Bank transfer")
        CASH = "cash", _("Cash")
        CHEQUE = "cheque", _("Cheque")
        CARD = "card", _("Card (online)")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        PENDING = "pending", _("Waiting for the studio to confirm")
        CONFIRMED = "confirmed", _("Confirmed")
        REJECTED = "rejected", _("Not received")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Deleting a sent invoice is refused in the services (drafts only); deleting a client deletes it all.
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=15, choices=Method.choices, default=Method.BANK_TRANSFER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED, db_index=True)
    received_on = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=120, blank=True, default="")
    note = models.TextField(blank=True, default="")
    provider = models.CharField(max_length=20, blank=True, default="")
    provider_reference = models.CharField(max_length=120, blank=True, default="", db_index=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_reference"],
                condition=~models.Q(provider_reference=""),
                name="billing_payment_provider_reference_once",
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="billing_payment_positive"),
        ]

    def __str__(self):
        return f"{self.currency} {self.amount} for {self.invoice}"


class PaymentProof(WorkspaceScopedModel, PrivateFile):
    """A transfer receipt the client uploads (or the studio attaches) for a payment."""

    STAFF_PERMISSION = "view_billing"
    PORTAL_PERMISSION = "portal_view_billing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="proofs")

    def client_can_see(self) -> bool:
        return True


class TaxDocument(WorkspaceScopedModel):
    """A certified tax document issued by an authorised provider for an invoice. The app never makes one up."""

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        ISSUED = "issued", _("Issued")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="tax_document")
    provider = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    official_number = models.CharField(max_length=120, blank=True, default="")
    issued_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(blank=True, default="")
    is_test = models.BooleanField(default=False, help_text="Issued by a mock provider: not valid for tax.")
    created_at = models.DateTimeField(auto_now_add=True)


class ReminderLog(models.Model):
    """One reminder per invoice per scheduled day, so a rerun never sends twice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="reminders")
    offset_days = models.SmallIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)
    recipients = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["invoice", "offset_days"], name="billing_reminder_once")]
