"""Billing: numbering, totals and statuses, sending, payments, credit notes, proposals, contracts, reminders.

Every change a client or the studio makes to a proposal, contract, invoice or
payment is written to the audit log.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.billing.adapters import provider
from apps.billing.models import (
    ZERO,
    BillingSettings,
    Contract,
    DocumentTemplate,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentProof,
    Proposal,
    ReminderLog,
    TaxDocument,
    money,
)

log = logging.getLogger(__name__)


class BillingError(ValueError):
    """A billing action that is not allowed in the document's current state."""


# ── Settings and templates ───────────────────────────────────────────────────


def settings_for(organization) -> BillingSettings:
    obj, created = BillingSettings.objects.get_or_create(
        organization=organization,
        defaults={"legal_name": organization.name, "reminder_days": list(BillingSettings.DEFAULT_REMINDERS)},
    )
    return obj


BUILTIN_TEMPLATES = {
    "proposal": (
        DocumentTemplate.Kind.PROPOSAL,
        "Standard proposal",
        "Thank you for considering {{ studio }}. This proposal sets out the work we will do for {{ client }}, "
        "what it costs and how we will start.",
    ),
    "contract": (
        DocumentTemplate.Kind.CONTRACT,
        "Services agreement",
        "SERVICES AGREEMENT\n\n"
        "Between {{ studio }} (the Studio) and {{ client }} (the Client), dated {{ date }}.\n\n"
        "1. Services. The Studio provides the services in proposal {{ proposal }}: {{ services }}.\n"
        "2. Fees. One-off fees of {{ currency }} {{ one_off_total }} and monthly fees of {{ currency }} "
        "{{ monthly_total }}. A deposit of {{ currency }} {{ deposit }} is due before work starts. Invoices are due "
        "{{ payment_terms }} days after they are issued.\n"
        "3. Approvals. Content is published only after the Client's approval in the client portal.\n"
        "4. Revisions. The number of revision rounds is the one in the proposal.\n"
        "5. Ownership. On full payment, the Client owns the final deliverables made for it. The Studio may show "
        "the work in its portfolio unless the Client asks it not to.\n"
        "6. Term. Monthly services continue until either party gives 30 days' notice in writing.\n"
        "7. Confidentiality. Each party keeps the other's non-public information confidential.\n\n"
        "This template is a starting point. Have it reviewed by a lawyer for your jurisdiction before use.",
    ),
}


def ensure_templates(organization) -> dict[str, DocumentTemplate]:
    found = {}
    for key, (kind, name, body) in BUILTIN_TEMPLATES.items():
        found[key], _ = DocumentTemplate.objects.get_or_create(
            organization=organization, builtin_key=key, defaults={"kind": kind, "name": name, "body": body}
        )
    return found


FIELD_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def fill(body: str, values: dict) -> str:
    """Replace ``{{ field }}`` with known values only (never a template engine on studio-written text)."""
    return FIELD_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), body)


def _client_name(workspace) -> str:
    from apps.clients.services import profile_for

    profile = profile_for(workspace)
    return profile.legal_name or workspace.name


# ── Numbering ────────────────────────────────────────────────────────────────


def _next_number(organization, kind: str) -> str:
    """Sequential per studio, gap-free within a transaction (the settings row is locked)."""
    settings_for(organization)
    row = BillingSettings.objects.select_for_update().get(organization=organization)
    prefix_field, counter_field = {
        "invoice": ("invoice_prefix", "next_invoice_number"),
        "credit_note": ("credit_note_prefix", "next_credit_note_number"),
        "proposal": ("proposal_prefix", "next_proposal_number"),
    }[kind]
    number = getattr(row, counter_field)
    setattr(row, counter_field, number + 1)
    row.save(update_fields=[counter_field, "updated_at"])
    return f"{getattr(row, prefix_field)}{number:04d}"


# ── Totals and status ────────────────────────────────────────────────────────


def compute_totals(invoice) -> dict:
    """Line discounts, then the invoice discount spread over lines by value, then tax per line."""
    lines = list(invoice.lines.all())
    subtotal = sum((line.gross for line in lines), ZERO)
    line_discounts = sum((line.discount for line in lines), ZERO)
    net_total = subtotal - line_discounts
    extra = min(Decimal(invoice.discount_amount or 0), net_total) if net_total > 0 else ZERO
    tax = ZERO
    for line in lines:
        base = line.gross - line.discount
        if net_total > 0 and extra:
            base -= extra * base / net_total
        tax += base * Decimal(line.tax_rate) / 100
    discount_total = line_discounts + extra
    total = subtotal - discount_total + tax
    return {
        "subtotal": money(subtotal),
        "discount_total": money(discount_total),
        "tax_total": money(tax),
        "total": money(total),
    }


def tax_breakdown(invoice) -> list[tuple[str, Decimal, Decimal]]:
    """(name, rate, amount) per tax, for the invoice and its PDF."""
    lines = list(invoice.lines.all())
    net_total = sum((line.gross - line.discount for line in lines), ZERO)
    extra = min(Decimal(invoice.discount_amount or 0), net_total) if net_total > 0 else ZERO
    groups: dict[tuple[str, Decimal], Decimal] = {}
    for line in lines:
        if not line.tax_rate:
            continue
        base = line.gross - line.discount
        if net_total > 0 and extra:
            base -= extra * base / net_total
        key = (line.tax_name or "Tax", Decimal(line.tax_rate))
        groups[key] = groups.get(key, ZERO) + base * Decimal(line.tax_rate) / 100
    return [(name, rate, money(amount)) for (name, rate), amount in groups.items()]


def recalculate(invoice, *, today: date | None = None, save=True) -> Invoice:
    for field, value in compute_totals(invoice).items():
        setattr(invoice, field, value)
    if invoice.is_credit_note:
        invoice.amount_paid = invoice.amount_credited = ZERO
        invoice.balance = ZERO
    else:
        invoice.amount_paid = money(
            sum(invoice.payments.filter(status=Payment.Status.CONFIRMED).values_list("amount", flat=True), ZERO)
        )
        invoice.amount_credited = money(
            sum(
                invoice.credit_notes.exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.VOID]).values_list(
                    "total", flat=True
                ),
                ZERO,
            )
        )
        invoice.balance = money(invoice.total - invoice.amount_paid - invoice.amount_credited)
    _refresh_status(invoice, today or timezone.localdate())
    if save:
        invoice.save()
    return invoice


def _refresh_status(invoice, today: date) -> None:
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
        return
    if invoice.is_credit_note:
        invoice.status = Invoice.Status.PAID  # issued and applied
        return
    if invoice.total > 0 and invoice.balance <= 0:
        if invoice.status != Invoice.Status.PAID:
            invoice.paid_at = timezone.now()
        invoice.status = Invoice.Status.PAID
    elif invoice.due_date and invoice.due_date < today:
        invoice.status = Invoice.Status.OVERDUE
    elif invoice.amount_paid > 0 or invoice.amount_credited > 0:
        invoice.status = Invoice.Status.PARTLY_PAID
    elif invoice.viewed_at:
        invoice.status = Invoice.Status.VIEWED
    else:
        invoice.status = Invoice.Status.SENT
    if invoice.status != Invoice.Status.PAID:
        invoice.paid_at = None


def _require_draft(invoice):
    if invoice.status != Invoice.Status.DRAFT:
        raise BillingError("Only a draft can be changed. Issue a credit note to correct a sent invoice.")


# ── Invoices ─────────────────────────────────────────────────────────────────


def create_invoice(
    workspace,
    actor,
    *,
    purpose=Invoice.Purpose.ONE_TIME,
    title="",
    lines=(),
    currency=None,
    exchange_rate=None,
    notes="",
    discount_amount=ZERO,
    proposal=None,
    retainer=None,
    period=(None, None),
) -> Invoice:
    """A draft invoice with the client's billing details copied in. ``lines`` are dicts for ``add_line``."""
    from apps.clients.services import profile_for

    profile = profile_for(workspace)
    studio = settings_for(workspace.organization)
    currency = (currency or profile.currency or studio.base_currency).upper()
    if currency == studio.base_currency:
        exchange_rate = Decimal("1")
    elif not exchange_rate or Decimal(exchange_rate) <= 0:
        raise BillingError(f"Enter the exchange rate: how many {studio.base_currency} one {currency} is worth.")
    invoice = Invoice.objects.create(
        workspace=workspace,
        purpose=purpose,
        title=title,
        currency=currency,
        base_currency=studio.base_currency,
        exchange_rate=Decimal(exchange_rate),
        notes=notes,
        discount_amount=money(discount_amount),
        proposal=proposal,
        retainer=retainer,
        period_start=period[0],
        period_end=period[1],
        bill_to_name=profile.legal_name or workspace.name,
        bill_to_address="\n".join(x for x in (profile.address, profile.city) if x),
        bill_to_tax_id=profile.tax_id,
        created_by=actor,
    )
    for line in lines:
        add_line(invoice, **line)
    recalculate(invoice)
    record(
        "invoice.created",
        f"Draft {invoice.get_purpose_display().lower()} invoice created",
        workspace=workspace,
        actor=actor,
        target=invoice,
    )
    return invoice


def add_line(
    invoice,
    *,
    description,
    quantity=1,
    unit_price=ZERO,
    discount_percent=ZERO,
    tax_rate=None,
    tax_name=None,
    service_kind="",
    catalog_item=None,
) -> InvoiceLine:
    _require_draft(invoice)
    studio = settings_for(invoice.workspace.organization)
    if Decimal(quantity) <= 0 or Decimal(unit_price) < 0 or not (0 <= Decimal(discount_percent) <= 100):
        raise BillingError("Quantity must be above zero, price not negative and the discount between 0 and 100%.")
    line = InvoiceLine.objects.create(
        invoice=invoice,
        description=description[:300],
        quantity=Decimal(quantity),
        unit_price=money(unit_price),
        discount_percent=Decimal(discount_percent),
        tax_rate=Decimal(studio.tax_rate if tax_rate is None else tax_rate),
        tax_name=(studio.tax_name if tax_name is None else tax_name)[:40],
        service_kind=service_kind,
        catalog_item=catalog_item,
        position=invoice.lines.count(),
    )
    return line


def billing_recipients(workspace) -> tuple[list, list[str]]:
    """Portal users who see billing, and billing contacts without a portal login (by email)."""
    from apps.clients.models import Contact
    from apps.studio.portal import client_users

    users = client_users(workspace, "portal_view_billing")
    emails = {u.email.lower() for u in users}
    others = [
        c.email
        for c in Contact.objects.for_workspace(workspace.id).filter(is_billing=True).exclude(email="")
        if c.email.lower() not in emails
    ]
    return users, others


def _notify_client(workspace, *, subject, template, context) -> list[str]:
    """Email billing people: portal users get a sign-in link; others the document's share link."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from apps.common.mail import send_or_raise, transactional
    from apps.studio.portal import send_portal_email

    users, emails = billing_recipients(workspace)
    sent = []
    for user in users:
        if send_portal_email(user, workspace, subject=subject, template=template, context=context):
            sent.append(user.email)
    for email in emails:
        ctx = {**context, "workspace": workspace, "site_name": settings.SITE_NAME, "portal_url": ""}
        msg = EmailMultiAlternatives(
            subject=subject,
            body=render_to_string(f"{template}.txt", ctx),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
            headers=transactional(),
        )
        msg.attach_alternative(render_to_string(f"{template}.html", ctx), "text/html")
        try:
            send_or_raise(msg)
            sent.append(email)
        except Exception:
            log.exception("Billing email to %s failed", email)
    return sent


def share_url(invoice) -> str:
    from django.urls import reverse

    return settings.APP_URL.rstrip("/") + reverse("billing_public:invoice", kwargs={"token": invoice.share_token})


@transaction.atomic
def send_invoice(invoice, actor, *, notify=True, today: date | None = None) -> Invoice:
    """Issue a draft: number, issue and due dates, status Sent, and an email to the client's billing people."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _require_draft(invoice)
    if not invoice.lines.exists():
        raise BillingError("Add at least one line before sending.")
    recalculate(invoice, save=False)
    if invoice.total <= 0:
        raise BillingError("The total must be above zero.")
    if invoice.is_credit_note:
        target = invoice.credits
        if target.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
            raise BillingError("A credit note needs a sent invoice to apply to.")
        if invoice.total > target.balance:
            raise BillingError(f"The credit is more than the balance left ({target.currency} {target.balance}).")
    today = today or timezone.localdate()
    studio = settings_for(invoice.workspace.organization)
    invoice.number = _next_number(invoice.workspace.organization, invoice.kind)
    invoice.issue_date = today
    if not invoice.due_date and not invoice.is_credit_note:
        invoice.due_date = today + timedelta(days=studio.payment_terms_days)
    invoice.status = Invoice.Status.SENT
    invoice.sent_at = timezone.now()
    recalculate(invoice, today=today)
    record(
        "invoice.sent",
        f"{invoice.get_kind_display()} {invoice.number} issued for {invoice.currency} {invoice.total}",
        workspace=invoice.workspace,
        actor=actor,
        target=invoice,
        data={"total": str(invoice.total), "currency": invoice.currency},
    )
    if invoice.is_credit_note:
        recalculate(invoice.credits, today=today)
        record(
            "invoice.credited",
            f"Credit note {invoice.number} applied to {invoice.credits.number}",
            workspace=invoice.workspace,
            actor=actor,
            target=invoice.credits,
        )
    if notify:
        transaction.on_commit(lambda: _notify_sent(invoice))
    return invoice


def _notify_sent(invoice):
    _notify_client(
        invoice.workspace,
        subject=f"{settings.SITE_NAME}: {invoice.get_kind_display().lower()} {invoice.number}",
        template="billing/email/invoice_sent",
        context={"invoice": invoice, "share_url": share_url(invoice)},
    )


def mark_viewed(invoice, user=None) -> bool:
    """The client opened it (portal or share link). Staff opening it never counts."""
    if invoice.viewed_at or invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
        return False
    invoice.viewed_at = timezone.now()
    recalculate(invoice)
    record(
        "invoice.viewed",
        f"{invoice.number} viewed by the client",
        workspace=invoice.workspace,
        actor=user,
        target=invoice,
    )
    return True


def _require_payable(invoice, amount) -> Decimal:
    if invoice.is_credit_note or not invoice.is_open:
        raise BillingError("Only a sent, unpaid invoice can take a payment.")
    amount = money(amount)
    if amount <= 0:
        raise BillingError("The amount must be above zero.")
    if amount > invoice.balance:
        raise BillingError(f"That is more than the balance ({invoice.currency} {invoice.balance}).")
    return amount


@transaction.atomic
def record_payment(
    invoice, actor, *, amount, method=Payment.Method.BANK_TRANSFER, received_on=None, reference="", note="", proof=None
) -> Payment:
    """The studio records money received (confirmed straight away)."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    amount = _require_payable(invoice, amount)
    payment = Payment.objects.create(
        workspace=invoice.workspace,
        invoice=invoice,
        amount=amount,
        currency=invoice.currency,
        method=method,
        received_on=received_on or timezone.localdate(),
        reference=reference[:120],
        note=note,
        status=Payment.Status.CONFIRMED,
        confirmed_by=actor,
        confirmed_at=timezone.now(),
    )
    if proof is not None:
        _attach_proof(payment, proof, actor)
    _after_payment(invoice, payment, actor, f"Payment of {invoice.currency} {amount} recorded for {invoice.number}")
    return payment


def _attach_proof(payment, upload, user) -> PaymentProof:
    from django.core.exceptions import ValidationError

    proof = PaymentProof(workspace=payment.workspace, payment=payment)
    try:
        proof.attach(upload, ["image", "pdf"], user=user, max_bytes=10 * 1024 * 1024)
    except ValidationError as exc:
        raise BillingError(" ".join(exc.messages)) from exc
    proof.save()
    return proof


@transaction.atomic
def submit_payment_proof(invoice, user, *, amount, upload, reference="", note="") -> Payment:
    """The client says they paid by transfer and uploads the receipt; the studio confirms it later."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    pending = sum(invoice.payments.filter(status=Payment.Status.PENDING).values_list("amount", flat=True), ZERO)
    amount = money(amount)
    if amount + pending > invoice.balance:
        raise BillingError(
            f"That is more than the balance still to confirm ({invoice.currency} {money(invoice.balance - pending)})."
        )
    amount = _require_payable(invoice, amount)
    payment = Payment.objects.create(
        workspace=invoice.workspace,
        invoice=invoice,
        amount=amount,
        currency=invoice.currency,
        method=Payment.Method.BANK_TRANSFER,
        reference=reference[:120],
        note=note,
        status=Payment.Status.PENDING,
        submitted_by=user,
    )
    _attach_proof(payment, upload, user)
    record(
        "payment.proof_submitted",
        f"Proof of payment for {invoice.currency} {amount} uploaded for {invoice.number}",
        workspace=invoice.workspace,
        actor=user,
        target=invoice,
    )
    transaction.on_commit(lambda: _notify_studio_proof(payment))
    return payment


def notify_billing_staff(workspace, event_type, title, body, data=None):
    """In-app (and per preference, email) notice to the client's staff who manage billing."""
    from apps.members.models import WorkspaceMembership
    from apps.notifications.engine import notify

    for m in WorkspaceMembership.objects.filter(workspace=workspace).select_related("user", "custom_role"):
        if m.effective_permissions.get("manage_billing", False):
            try:
                notify(user=m.user, event_type=event_type, title=title, body=body, data=data or {})
            except Exception:
                log.exception("Billing notification to %s failed", m.user_id)


def _notify_studio_proof(payment):
    notify_billing_staff(
        payment.workspace,
        "payment_submitted",
        f"Payment to confirm: {payment.invoice.number}",
        f"{payment.workspace.name} uploaded proof of {payment.currency} {payment.amount}.",
        {"invoice_id": str(payment.invoice_id)},
    )


@transaction.atomic
def confirm_payment(payment, actor) -> Payment:
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != Payment.Status.PENDING:
        raise BillingError("This payment is not waiting for confirmation.")
    invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
    if payment.amount > invoice.balance:
        raise BillingError(f"That is more than the balance ({invoice.currency} {invoice.balance}).")
    payment.status = Payment.Status.CONFIRMED
    payment.confirmed_by, payment.confirmed_at = actor, timezone.now()
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    _after_payment(
        invoice, payment, actor, f"Payment of {payment.currency} {payment.amount} confirmed for {invoice.number}"
    )
    return payment


def reject_payment(payment, actor, reason: str) -> Payment:
    if payment.status != Payment.Status.PENDING:
        raise BillingError("This payment is not waiting for confirmation.")
    if not reason.strip():
        raise BillingError("Say why, so the client knows what to do.")
    payment.status = Payment.Status.REJECTED
    payment.rejection_reason = reason.strip()
    payment.confirmed_by, payment.confirmed_at = actor, timezone.now()
    payment.save(update_fields=["status", "rejection_reason", "confirmed_by", "confirmed_at"])
    record(
        "payment.rejected",
        f"Payment of {payment.currency} {payment.amount} for {payment.invoice.number} not received: {reason.strip()}",
        workspace=payment.workspace,
        actor=actor,
        target=payment.invoice,
    )
    return payment


def _after_payment(invoice, payment, actor, summary):
    recalculate(invoice)
    record(
        "payment.recorded",
        summary,
        workspace=invoice.workspace,
        actor=actor,
        target=invoice,
        data={"amount": str(payment.amount), "method": payment.method, "balance": str(invoice.balance)},
    )
    if invoice.status == Invoice.Status.PAID and invoice.purpose == Invoice.Purpose.DEPOSIT:
        _onboarding_step(invoice.workspace, "deposit", actor)


def payment_provider():
    return provider("payment")


def start_checkout(invoice, user, *, return_url):
    amount = _require_payable(invoice, invoice.balance)
    checkout = payment_provider().start_checkout(invoice, amount, return_url=return_url)
    record(
        "payment.checkout_started",
        f"Online payment started for {invoice.number}",
        workspace=invoice.workspace,
        actor=user,
        target=invoice,
    )
    return checkout


@transaction.atomic
def complete_online_payment(invoice, user, *, reference, amount, provider_key) -> Payment:
    """Called when a provider confirms a payment. Idempotent per provider reference."""
    existing = Payment.objects.filter(provider=provider_key, provider_reference=reference).first()
    if existing:
        return existing
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    amount = _require_payable(invoice, amount)
    payment = Payment.objects.create(
        workspace=invoice.workspace,
        invoice=invoice,
        amount=amount,
        currency=invoice.currency,
        method=Payment.Method.CARD,
        provider=provider_key,
        provider_reference=reference,
        status=Payment.Status.CONFIRMED,
        submitted_by=user,
        confirmed_at=timezone.now(),
        note="Test payment: no money moved." if provider_key == "mock" else "",
    )
    _after_payment(invoice, payment, user, f"Online payment of {invoice.currency} {amount} for {invoice.number}")
    return payment


@transaction.atomic
def void_invoice(invoice, actor, reason: str) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == Invoice.Status.VOID:
        return invoice
    if invoice.payments.filter(status=Payment.Status.CONFIRMED).exists() or invoice.amount_credited > 0:
        raise BillingError("It has payments or credits. Issue a credit note for what is left instead.")
    if not reason.strip():
        raise BillingError("Give a reason for voiding.")
    invoice.status = Invoice.Status.VOID
    invoice.voided_at, invoice.void_reason = timezone.now(), reason.strip()
    invoice.save(update_fields=["status", "voided_at", "void_reason", "updated_at"])
    invoice.payments.filter(status=Payment.Status.PENDING).update(
        status=Payment.Status.REJECTED, rejection_reason="Invoice voided."
    )
    record(
        "invoice.voided",
        f"{invoice} voided: {reason.strip()}",
        workspace=invoice.workspace,
        actor=actor,
        target=invoice,
    )
    if invoice.is_credit_note and invoice.credits_id:
        recalculate(invoice.credits)
    return invoice


def create_credit_note(invoice, actor, *, amount, reason) -> Invoice:
    """A draft credit note against a sent invoice, for ``amount`` (tax included)."""
    if invoice.is_credit_note or invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
        raise BillingError("Credit notes apply to sent invoices.")
    amount = money(amount)
    if amount <= 0 or amount > invoice.balance:
        raise BillingError(
            f"The credit must be above zero and at most the balance ({invoice.currency} {invoice.balance})."
        )
    if not reason.strip():
        raise BillingError("Say what the credit is for.")
    note = Invoice.objects.create(
        workspace=invoice.workspace,
        kind=Invoice.Kind.CREDIT_NOTE,
        credits=invoice,
        purpose=invoice.purpose,
        title=f"Credit against {invoice.number}",
        currency=invoice.currency,
        base_currency=invoice.base_currency,
        exchange_rate=invoice.exchange_rate,
        notes=reason.strip(),
        bill_to_name=invoice.bill_to_name,
        bill_to_address=invoice.bill_to_address,
        bill_to_tax_id=invoice.bill_to_tax_id,
        created_by=actor,
    )
    add_line(note, description=reason.strip()[:300], unit_price=amount, tax_rate=0, tax_name="")
    recalculate(note)
    record(
        "invoice.credit_drafted",
        f"Draft credit note of {invoice.currency} {amount} against {invoice.number}",
        workspace=invoice.workspace,
        actor=actor,
        target=invoice,
    )
    return note


def request_tax_document(invoice, actor) -> TaxDocument:
    tax = provider("tax")
    if not tax.enabled:
        raise BillingError("No certified tax-document provider is set up (BILLING_TAX_PROVIDER).")
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
        raise BillingError("Issue the invoice first.")
    if hasattr(invoice, "tax_document"):
        return invoice.tax_document
    doc = TaxDocument.objects.create(
        workspace=invoice.workspace, invoice=invoice, provider=tax.key, is_test=tax.is_test
    )
    try:
        issued = tax.issue(invoice)
    except Exception as exc:  # the provider's own error, kept for the studio
        doc.status, doc.detail = TaxDocument.Status.FAILED, str(exc)[:1000]
        doc.save(update_fields=["status", "detail"])
        raise BillingError(f"The tax-document provider refused it: {exc}") from exc
    doc.status, doc.official_number, doc.issued_at, doc.detail = (
        TaxDocument.Status.ISSUED,
        issued.official_number,
        issued.issued_at,
        issued.detail,
    )
    doc.save()
    record(
        "invoice.tax_document",
        f"Tax document {doc.official_number} issued for {invoice.number}" + (" (test)" if doc.is_test else ""),
        workspace=invoice.workspace,
        actor=actor,
        target=invoice,
    )
    return doc


# ── Onboarding follows billing ───────────────────────────────────────────────


def _onboarding_step(workspace, key: str, actor) -> None:
    """Tick the open onboarding run's proposal, contract or deposit step."""
    from apps.client_onboarding.models import OnboardingRun, OnboardingStep
    from apps.client_onboarding.services import finish_step

    run = OnboardingRun.objects.filter(workspace=workspace, status=OnboardingRun.Status.IN_PROGRESS).first()
    step = run.steps.filter(key=key).first() if run else None
    if step and not step.is_finished:
        finish_step(step, actor, status=OnboardingStep.Status.DONE, actor_is_client=False)


# ── Proposals ────────────────────────────────────────────────────────────────


def create_proposal(workspace, actor, *, title, introduction=None, currency=None, services=(), lines=(), **extra):
    from apps.clients.services import profile_for

    templates = ensure_templates(workspace.organization)
    studio = settings_for(workspace.organization)
    if introduction is None:
        introduction = fill(
            templates["proposal"].body, {"studio": studio.legal_name, "client": _client_name(workspace)}
        )
    proposal = Proposal.objects.create(
        workspace=workspace,
        title=title,
        introduction=introduction,
        currency=(currency or profile_for(workspace).currency or studio.base_currency).upper(),
        services=list(services),
        contract_template=extra.pop("contract_template", templates["contract"]),
        created_by=actor,
        **extra,
    )
    from apps.billing.models import ProposalLine

    for position, line in enumerate(lines):
        ProposalLine.objects.create(proposal=proposal, position=position, **line)
    record("proposal.created", f"Proposal “{title}” drafted", workspace=workspace, actor=actor, target=proposal)
    return proposal


@transaction.atomic
def send_proposal(proposal, actor, *, notify=True) -> Proposal:
    from apps.clients.models import ClientProfile
    from apps.clients.services import change_stage, profile_for

    if proposal.status != Proposal.Status.DRAFT:
        raise BillingError("This proposal was already sent.")
    if not proposal.lines.exists():
        raise BillingError("Add at least one service line.")
    proposal.number = _next_number(proposal.workspace.organization, "proposal")
    proposal.status, proposal.sent_at = Proposal.Status.SENT, timezone.now()
    if not proposal.valid_until:
        proposal.valid_until = timezone.localdate() + timedelta(days=30)
    proposal.save()
    profile = profile_for(proposal.workspace)
    if profile.stage == ClientProfile.Stage.LEAD:
        change_stage(profile, ClientProfile.Stage.PROPOSAL_SENT, actor)
    record(
        "proposal.sent", f"Proposal {proposal.number} sent", workspace=proposal.workspace, actor=actor, target=proposal
    )
    if notify:
        transaction.on_commit(
            lambda: _notify_client(
                proposal.workspace,
                subject=f"{settings.SITE_NAME}: proposal {proposal.number}",
                template="billing/email/proposal_sent",
                context={"proposal": proposal},
            )
        )
    return proposal


def mark_proposal_viewed(proposal, user) -> None:
    if proposal.status == Proposal.Status.SENT:
        proposal.status, proposal.viewed_at = Proposal.Status.VIEWED, timezone.now()
        proposal.save(update_fields=["status", "viewed_at", "updated_at"])
        record(
            "proposal.viewed",
            f"Proposal {proposal.number} viewed by the client",
            workspace=proposal.workspace,
            actor=user,
            target=proposal,
        )


def _contacts_for_onboarding(workspace):
    from apps.clients.models import Contact

    contacts = list(Contact.objects.for_workspace(workspace.id).exclude(email="").order_by("-is_primary", "name"))
    return [c for c in contacts if c.is_primary or c.is_billing or c.is_approver] or contacts[:1]


@transaction.atomic
def accept_proposal(proposal, user, *, name: str, agreed: bool) -> dict:
    """The client accepts: the contract, the deposit invoice, the services, then onboarding or projects.

    Returns what was created: {"contract", "invoice", "run", "projects"}.
    """
    from apps.clients.models import ClientProfile, Retainer, ServiceLine
    from apps.clients.services import change_stage, profile_for

    proposal = Proposal.objects.select_for_update().get(pk=proposal.pk)
    if not proposal.is_open:
        raise BillingError("This proposal is no longer open.")
    if proposal.valid_until and proposal.valid_until < timezone.localdate():
        raise BillingError("This proposal has expired. Ask the studio for a new one.")
    if not agreed or not name.strip():
        raise BillingError("Type your full name and tick the box to accept.")
    workspace = proposal.workspace
    proposal.status, proposal.decided_at, proposal.decided_by = Proposal.Status.ACCEPTED, timezone.now(), user
    proposal.accepted_name = name.strip()[:160]
    proposal.save()
    record(
        "proposal.accepted",
        f"Proposal {proposal.number} accepted by {proposal.accepted_name}",
        workspace=workspace,
        actor=user,
        target=proposal,
        data={"one_off": str(proposal.one_off_total()), "monthly": str(proposal.monthly_total())},
    )
    created: dict = {"contract": None, "invoice": None, "run": None, "projects": []}
    today = timezone.localdate()

    for kind in proposal.services:
        if kind in ServiceLine.Kind.values:
            ServiceLine.objects.update_or_create(
                workspace=workspace, kind=kind, defaults={"status": ServiceLine.Status.ACTIVE, "started_on": today}
            )
    monthly = proposal.monthly_total()
    if monthly > 0 and not Retainer.current_for(workspace):
        Retainer.objects.create(
            workspace=workspace,
            name=proposal.title[:120],
            monthly_fee=monthly,
            currency=proposal.currency,
            starts_on=today,
        )

    if proposal.contract_template_id:
        created["contract"] = create_contract(workspace, user, proposal=proposal, send=True)
    deposit = proposal.deposit_amount()
    if deposit > 0:
        invoice = create_invoice(
            workspace,
            None,
            purpose=Invoice.Purpose.DEPOSIT,
            title=f"Deposit for {proposal.number}",
            currency=proposal.currency,
            exchange_rate=_rate_for(workspace, proposal.currency),
            proposal=proposal,
            lines=[
                {"description": f"Deposit ({proposal.deposit_percent:g}%) for {proposal.title}", "unit_price": deposit}
            ],
        )
        created["invoice"] = send_invoice(invoice, None)

    _onboarding_step(workspace, "proposal", user)
    profile = profile_for(workspace)
    from apps.client_onboarding.models import OnboardingRun

    has_run = OnboardingRun.objects.filter(workspace=workspace).exists()
    if proposal.start_onboarding and not has_run:
        from apps.client_onboarding.services import start_run

        contacts = _contacts_for_onboarding(workspace)
        if contacts:
            run, _problems = start_run(workspace, proposal.created_by, services=proposal.services, contacts=contacts)
            created["run"] = run
            for key, done in (("proposal", True), ("contract", False), ("deposit", deposit <= 0)):
                if done:
                    _onboarding_step(workspace, key, user)
    elif has_run:
        created["projects"] = _projects_for(proposal, today)
    if profile.stage in (ClientProfile.Stage.LEAD, ClientProfile.Stage.PROPOSAL_SENT):
        change_stage(profile, ClientProfile.Stage.ONBOARDING if created["run"] else ClientProfile.Stage.ACTIVE, user)
    transaction.on_commit(
        lambda: notify_billing_staff(
            workspace,
            "proposal_accepted",
            f"{workspace.name} accepted {proposal.number}",
            f"{proposal.accepted_name} accepted “{proposal.title}”.",
            {"proposal_id": str(proposal.id)},
        )
    )
    return created


def _rate_for(workspace, currency):
    studio = settings_for(workspace.organization)
    if currency == studio.base_currency:
        return Decimal("1")
    last = (
        Invoice.objects.filter(workspace__organization=workspace.organization, currency=currency)
        .exclude(exchange_rate=1)
        .order_by("-created_at")
        .first()
    )
    if last is None:
        raise BillingError(f"No exchange rate on record for {currency}; create the deposit invoice by hand.")
    return last.exchange_rate


def _projects_for(proposal, today):
    """A client already onboarded buying more: a project per service, from the matching template."""
    from apps.projects.services import create_from_template, ensure_builtin_templates

    templates = ensure_builtin_templates(proposal.workspace.organization)
    keys = {"social_media": "social_retainer", "branding": "brand", "brand_strategy": "strategy"}
    made = []
    for kind in proposal.services:
        template = templates.get(keys.get(kind, ""))
        if template:
            made.append(
                create_from_template(
                    proposal.workspace,
                    template,
                    name=f"{proposal.title}: {template.name}"[:200],
                    starts_on=today,
                    owner=proposal.created_by,
                    created_by=proposal.created_by,
                )
            )
    return made


def decline_proposal(proposal, user, reason="") -> Proposal:
    if not proposal.is_open:
        raise BillingError("This proposal is no longer open.")
    proposal.status, proposal.decided_at, proposal.decided_by = Proposal.Status.DECLINED, timezone.now(), user
    proposal.decline_reason = reason.strip()
    proposal.save()
    record(
        "proposal.declined",
        f"Proposal {proposal.number} declined" + (f": {proposal.decline_reason}" if proposal.decline_reason else ""),
        workspace=proposal.workspace,
        actor=user,
        target=proposal,
    )
    return proposal


# ── Contracts ────────────────────────────────────────────────────────────────


def create_contract(workspace, actor, *, proposal=None, template=None, title="", send=False) -> Contract:
    studio = settings_for(workspace.organization)
    template = (
        template
        or (proposal.contract_template if proposal else None)
        or ensure_templates(workspace.organization)["contract"]
    )
    from apps.clients.models import ServiceLine

    services = (
        ", ".join(dict(ServiceLine.Kind.choices).get(k, k) for k in (proposal.services if proposal else []))
        or "as agreed in writing"
    )
    body = fill(
        template.body,
        {
            "studio": studio.legal_name or workspace.organization.name,
            "client": _client_name(workspace),
            "date": timezone.localdate().strftime("%-d %B %Y"),
            "proposal": proposal.number if proposal else "",
            "services": services,
            "currency": proposal.currency if proposal else studio.base_currency,
            "one_off_total": proposal.one_off_total() if proposal else "0.00",
            "monthly_total": proposal.monthly_total() if proposal else "0.00",
            "deposit": proposal.deposit_amount() if proposal else "0.00",
            "payment_terms": studio.payment_terms_days,
        },
    )
    contract = Contract.objects.create(
        workspace=workspace,
        proposal=proposal,
        title=title or (f"Services agreement · {proposal.number}" if proposal else template.name),
        body=body,
        created_by=actor,
    )
    record(
        "contract.created", f"Contract “{contract.title}” drafted", workspace=workspace, actor=actor, target=contract
    )
    if send:
        send_contract(contract, actor)
    return contract


def send_contract(contract, actor, *, notify=True) -> Contract:
    if contract.status != Contract.Status.DRAFT:
        raise BillingError("Only a draft contract can be sent.")
    if not contract.body.strip():
        raise BillingError("The contract has no text.")
    contract.status, contract.sent_at = Contract.Status.SENT, timezone.now()
    contract.save(update_fields=["status", "sent_at", "updated_at"])
    record(
        "contract.sent", f"Contract “{contract.title}” sent for signature", workspace=contract.workspace,
        actor=actor, target=contract,
    )  # fmt: skip
    if notify:
        transaction.on_commit(
            lambda: _notify_client(
                contract.workspace,
                subject=f"{settings.SITE_NAME}: a contract to sign",
                template="billing/email/contract_sent",
                context={"contract": contract},
            )
        )
    return contract


def mark_contract_viewed(contract, user) -> None:
    if contract.status == Contract.Status.SENT:
        contract.status, contract.viewed_at = Contract.Status.VIEWED, timezone.now()
        contract.save(update_fields=["status", "viewed_at", "updated_at"])


@transaction.atomic
def sign_contract(contract, user, *, name: str, agreed: bool, ip=None, user_agent="") -> Contract:
    contract = Contract.objects.select_for_update().get(pk=contract.pk)
    if not contract.is_open:
        raise BillingError("This contract is not waiting for a signature.")
    if not agreed or not name.strip():
        raise BillingError("Type your full name and tick the box to sign.")
    result = provider("signature").sign(contract, name=name.strip(), user=user, ip=ip, user_agent=user_agent)
    contract.status, contract.signed_at = Contract.Status.SIGNED, timezone.now()
    contract.signer_user, contract.signer_name = user, name.strip()[:160]
    contract.signer_email = getattr(user, "email", "") or ""
    contract.signer_ip, contract.signer_user_agent = ip, (user_agent or "")[:300]
    contract.document_hash, contract.provider, contract.provider_reference = (
        result.document_hash,
        result.provider,
        result.reference,
    )
    contract.save()
    record(
        "contract.signed",
        f"Contract “{contract.title}” signed by {contract.signer_name}",
        workspace=contract.workspace,
        actor=user,
        target=contract,
        data={"hash": contract.document_hash, "provider": contract.provider, "ip": ip or ""},
    )
    _onboarding_step(contract.workspace, "contract", user)
    transaction.on_commit(
        lambda: notify_billing_staff(
            contract.workspace,
            "contract_signed",
            f"{contract.workspace.name} signed “{contract.title}”",
            f"Signed by {contract.signer_name}.",
            {"contract_id": str(contract.id)},
        )
    )
    return contract


def void_contract(contract, actor, reason: str) -> Contract:
    contract.refresh_from_db(fields=["status"])
    if contract.status == Contract.Status.SIGNED:
        raise BillingError("A signed contract stays on record. Agree a new one to replace it.")
    contract.status = Contract.Status.VOID
    contract.save(update_fields=["status", "updated_at"])
    record("contract.voided", f"Contract “{contract.title}” voided: {reason}", workspace=contract.workspace,
           actor=actor, target=contract)  # fmt: skip
    return contract


# ── Recurring work: overdue, reminders, retainers ────────────────────────────


def refresh_overdue(today: date | None = None) -> int:
    today = today or timezone.localdate()
    changed = 0
    for invoice in Invoice.objects.filter(
        kind=Invoice.Kind.INVOICE, status__in=[Invoice.Status.SENT, Invoice.Status.VIEWED, Invoice.Status.PARTLY_PAID],
        due_date__lt=today,
    ):  # fmt: skip
        recalculate(invoice, today=today)
        changed += invoice.status == Invoice.Status.OVERDUE
    return changed


def send_reminders(today: date | None = None, grace_days: int = 3) -> int:
    """For each open invoice, the schedule's reminders that are due (or missed in the last few days), once each."""
    today = today or timezone.localdate()
    sent = 0
    for invoice in (
        Invoice.objects.filter(kind=Invoice.Kind.INVOICE, status__in=Invoice.OPEN_STATUSES)
        .exclude(due_date=None)
        .select_related("workspace__organization")
    ):
        schedule = settings_for(invoice.workspace.organization).reminder_days or []
        due_offsets = [
            d
            for d in schedule
            if today - timedelta(days=grace_days) <= invoice.due_date + timedelta(days=int(d)) <= today
        ]
        if not due_offsets:
            continue
        offset = max(due_offsets)  # only the latest; never a burst after downtime
        if ReminderLog.objects.filter(invoice=invoice, offset_days__gte=offset).exists():
            continue
        recipients = _notify_client(
            invoice.workspace,
            subject=(
                f"{settings.SITE_NAME}: {invoice.number} is due "
                + ("today" if offset == 0 else f"on {invoice.due_date:%d %b}")
                if offset <= 0
                else f"{settings.SITE_NAME}: {invoice.number} is overdue"
            ),
            template="billing/email/reminder",
            context={"invoice": invoice, "offset": offset, "share_url": share_url(invoice)},
        )
        ReminderLog.objects.create(invoice=invoice, offset_days=offset, recipients=recipients)
        record(
            "invoice.reminder_sent",
            f"Reminder for {invoice.number} sent ({abs(offset)} day{'s' if abs(offset) != 1 else ''} "
            f"{'before' if offset < 0 else 'after'} the due date)"
            if offset
            else f"Reminder for {invoice.number} sent (due today)",
            workspace=invoice.workspace,
            actor=None,
            target=invoice,
        )
        sent += 1
    return sent


def draft_retainer_invoices(today: date | None = None) -> list[Invoice]:
    """On or after the 1st: a draft for each active retainer's month, once. The studio reviews and sends it."""
    from apps.clients.models import Retainer

    today = today or timezone.localdate()
    start = today.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    made = []
    for retainer in Retainer.objects.filter(is_active=True, starts_on__lte=end).select_related("workspace"):
        if retainer.ends_on and retainer.ends_on < start or retainer.monthly_fee <= 0:
            continue
        if Invoice.objects.filter(retainer=retainer, period_start=start).exists():
            continue
        try:
            made.append(
                create_invoice(
                    retainer.workspace,
                    None,
                    purpose=Invoice.Purpose.RETAINER,
                    title=f"{retainer.name}, {start:%B %Y}",
                    currency=retainer.currency,
                    exchange_rate=_rate_for(retainer.workspace, retainer.currency),
                    retainer=retainer,
                    period=(start, end),
                    lines=[
                        {
                            "description": f"{retainer.name}, {start:%B %Y}",
                            "unit_price": retainer.monthly_fee,
                            "service_kind": "social_media",
                        }
                    ],
                )
            )
        except BillingError as exc:
            log.warning("Retainer draft for %s skipped: %s", retainer.workspace, exc)
    return made


def run_daily(today: date | None = None) -> dict:
    return {
        "overdue": refresh_overdue(today),
        "reminders": send_reminders(today),
        "retainer_drafts": len(draft_retainer_invoices(today)),
    }


# ── Finance ──────────────────────────────────────────────────────────────────


def finance_summary(workspaces, today: date | None = None) -> dict:
    """Recurring revenue, outstanding and overdue balances, revenue by client and service, in the base currency."""
    from collections import defaultdict

    from apps.clients.models import Retainer

    today = today or timezone.localdate()
    ids = [w.id for w in workspaces]
    year_start = today.replace(month=1, day=1)
    invoices = list(
        Invoice.objects.filter(workspace_id__in=ids, kind=Invoice.Kind.INVOICE)
        .exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.VOID])
        .select_related("workspace")
        .prefetch_related("lines")
    )
    outstanding = sum((i.balance_in_base for i in invoices if i.is_open), ZERO)
    overdue = sum((i.balance_in_base for i in invoices if i.status == Invoice.Status.OVERDUE), ZERO)
    by_client: dict = defaultdict(lambda: ZERO)
    by_service: dict = defaultdict(lambda: ZERO)
    for i in invoices:
        if i.issue_date and i.issue_date >= year_start:
            by_client[i.workspace.name] += i.total_in_base
            for line in i.lines.all():
                by_service[line.service_kind or "other"] += money(line.net * Decimal(i.exchange_rate))
    payments = Payment.objects.filter(
        workspace_id__in=ids, status=Payment.Status.CONFIRMED, received_on__gte=year_start
    ).select_related("invoice")
    collected = sum((money(p.amount * p.invoice.exchange_rate) for p in payments), ZERO)
    mrr = ZERO
    for w in workspaces:
        r = Retainer.current_for(w)
        if r:
            rate = (
                Decimal("1") if r.currency == settings_for(w.organization).base_currency else _safe_rate(w, r.currency)
            )
            mrr += money(r.monthly_fee * rate)
    return {
        "mrr": money(mrr),
        "outstanding": money(outstanding),
        "overdue": money(overdue),
        "collected_ytd": money(collected),
        "by_client": sorted(((k, money(v)) for k, v in by_client.items()), key=lambda x: -x[1]),
        "by_service": sorted(((k, money(v)) for k, v in by_service.items()), key=lambda x: -x[1]),
        "overdue_invoices": sorted(
            (i for i in invoices if i.status == Invoice.Status.OVERDUE), key=lambda i: i.due_date or today
        ),
    }


def _safe_rate(workspace, currency):
    try:
        return _rate_for(workspace, currency)
    except BillingError:
        return Decimal("1")
