"""Billing services: totals, numbering, statuses, payments, credit notes, proposals, contracts, reminders."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.billing import services
from apps.billing.models import Contract, Invoice, Payment, Proposal, ProposalLine, ReminderLog
from apps.billing.services import BillingError
from apps.clients.models import Contact, Retainer, ServiceLine
from apps.studio.tests.helpers import make_studio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    s = services.settings_for(people["org"])
    s.tax_name, s.tax_rate, s.bank_details = "GST", Decimal("12.5"), "Demo Bank · 0001"
    s.save()
    return people


def _invoice(studio, lines=None, **kw):
    lines = lines or [{"description": "Social media, December", "unit_price": "1000.00"}]
    return services.create_invoice(studio["restaurant"], studio["owner"], lines=lines, **kw)


def _today():
    return timezone.localdate()


# ── Totals ───────────────────────────────────────────────────────────────────


def test_totals_with_line_discount_invoice_discount_and_tax(studio):
    invoice = _invoice(
        studio,
        lines=[
            {"description": "Reels", "quantity": 4, "unit_price": "150.00", "discount_percent": 10},  # 600 - 60
            {"description": "Shoot day", "unit_price": "460.00", "tax_rate": 0},
        ],
        discount_amount="100.00",
    )
    # net 540 + 460 = 1000; the 100 discount is spread 54 / 46; tax 12.5% of 486 = 60.75
    assert invoice.subtotal == Decimal("1060.00")
    assert invoice.discount_total == Decimal("160.00")
    assert invoice.tax_total == Decimal("60.75")
    assert invoice.total == Decimal("960.75")
    assert services.tax_breakdown(invoice) == [("GST", Decimal("12.500"), Decimal("60.75"))]


def test_rounding_is_half_up_to_the_cent(studio):
    invoice = _invoice(studio, lines=[{"description": "x", "quantity": 3, "unit_price": "0.33", "tax_rate": "12.5"}])
    assert invoice.total == Decimal("1.11")  # 0.99 + 0.12375 → 1.11


def test_foreign_currency_needs_and_keeps_an_exchange_rate(studio):
    with pytest.raises(BillingError, match="exchange rate"):
        _invoice(studio, currency="USD")
    invoice = _invoice(studio, currency="USD", exchange_rate="2.000000")
    assert invoice.base_currency == "BZD" and invoice.total_in_base == invoice.total * 2


# ── Sending and statuses ─────────────────────────────────────────────────────


def test_sending_numbers_dates_audits_and_emails(studio):
    Contact.objects.create(workspace=studio["restaurant"], name="Ana", email="ana@casamaya.test", is_billing=True)
    first, second = _invoice(studio), _invoice(studio)
    first = services.send_invoice(first, studio["owner"])
    second = services.send_invoice(second, studio["owner"])
    assert (first.number, second.number) == ("INV-0001", "INV-0002")
    assert first.status == "sent" and first.due_date == _today() + timedelta(days=14)
    assert AuditEvent.objects.filter(verb="invoice.sent", target_id=str(first.pk)).exists()
    with pytest.raises(BillingError, match="draft"):
        services.add_line(first, description="late change", unit_price=1)


def test_status_follows_viewing_payments_and_the_due_date(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    services.mark_viewed(invoice, studio["restaurant_client"])
    assert invoice.status == "viewed"
    services.record_payment(invoice, studio["owner"], amount="500.00")
    invoice.refresh_from_db()
    assert invoice.status == "partly_paid" and invoice.balance == Decimal("625.00")
    services.recalculate(invoice, today=invoice.due_date + timedelta(days=1))
    assert invoice.status == "overdue"
    services.record_payment(invoice, studio["owner"], amount="625.00")
    invoice.refresh_from_db()
    assert invoice.status == "paid" and invoice.balance == 0 and invoice.paid_at


def test_payments_cannot_exceed_the_balance(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    with pytest.raises(BillingError, match="balance"):
        services.record_payment(invoice, studio["owner"], amount="2000.00")
    with pytest.raises(BillingError, match="above zero"):
        services.record_payment(invoice, studio["owner"], amount="0")


def test_proof_of_payment_waits_for_the_studio(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    payment = services.submit_payment_proof(
        invoice, studio["restaurant_client"], amount="1125.00", upload=SimpleUploadedFile("receipt.png", PNG)
    )
    invoice.refresh_from_db()
    assert payment.status == "pending" and invoice.balance == Decimal("1125.00") and payment.proofs.count() == 1
    with pytest.raises(BillingError, match="still to confirm"):
        services.submit_payment_proof(
            invoice, studio["restaurant_client"], amount="1.00", upload=SimpleUploadedFile("r.png", PNG)
        )
    services.confirm_payment(payment, studio["owner"])
    invoice.refresh_from_db()
    assert invoice.status == "paid"


def test_rejected_proof_leaves_the_balance(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    payment = services.submit_payment_proof(
        invoice, studio["restaurant_client"], amount="100.00", upload=SimpleUploadedFile("r.png", PNG)
    )
    with pytest.raises(BillingError, match="why"):
        services.reject_payment(payment, studio["owner"], "")
    services.reject_payment(payment, studio["owner"], "Nothing arrived in the account")
    invoice.refresh_from_db()
    assert invoice.balance == Decimal("1125.00") and payment.status == "rejected"


def test_online_payment_is_recorded_once_per_reference(studio, settings):
    settings.BILLING_PAYMENT_PROVIDER = "mock"
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    one = services.complete_online_payment(
        invoice, studio["restaurant_client"], reference="mock_1", amount="1125.00", provider_key="mock"
    )
    two = services.complete_online_payment(
        invoice, studio["restaurant_client"], reference="mock_1", amount="1125.00", provider_key="mock"
    )
    invoice.refresh_from_db()
    assert one.pk == two.pk and Payment.objects.count() == 1 and invoice.status == "paid"


# ── Credit notes and voiding ─────────────────────────────────────────────────


def test_credit_note_reduces_the_balance(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    with pytest.raises(BillingError):
        services.create_credit_note(invoice, studio["owner"], amount="5000", reason="Too much")
    note = services.create_credit_note(invoice, studio["owner"], amount="125.00", reason="One reel not delivered")
    services.send_invoice(note, studio["owner"], notify=False)
    invoice.refresh_from_db()
    note.refresh_from_db()
    assert note.number == "CN-0001" and note.kind == "credit_note"
    assert invoice.amount_credited == Decimal("125.00") and invoice.balance == Decimal("1000.00")


def test_void_only_without_payments(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    services.record_payment(invoice, studio["owner"], amount="10")
    with pytest.raises(BillingError, match="credit note"):
        services.void_invoice(invoice, studio["owner"], "Wrong client")
    other = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    services.void_invoice(other, studio["owner"], "Duplicate")
    other.refresh_from_db()
    assert other.status == "void" and AuditEvent.objects.filter(verb="invoice.voided").exists()


# ── Proposals and contracts ──────────────────────────────────────────────────


def _proposal(studio, **kw):
    ws = studio["skincare"]
    Contact.objects.create(
        workspace=ws, name="Lia", email="lia@luma.test", is_primary=True, user=studio["skincare_client"]
    )
    proposal = services.create_proposal(
        ws,
        studio["owner"],
        title="Luma social and brand refresh",
        services=["social_media", "branding"],
        lines=[
            {"description": "Brand refresh", "unit_price": Decimal("3000.00")},
            {"description": "Social media management", "unit_price": Decimal("1800.00"), "recurring": True},
        ],
        **kw,
    )
    return services.send_proposal(proposal, studio["owner"], notify=False)


def test_accepting_a_proposal_creates_contract_deposit_services_and_onboarding(studio):
    proposal = _proposal(studio)
    assert proposal.number == "P-0001" and proposal.one_off_total() == Decimal("3000.00")
    with pytest.raises(BillingError, match="name"):
        services.accept_proposal(proposal, studio["skincare_client"], name="", agreed=True)
    made = services.accept_proposal(proposal, studio["skincare_client"], name="Lia Chen", agreed=True)
    ws = studio["skincare"]
    proposal.refresh_from_db()
    assert proposal.status == "accepted" and proposal.accepted_name == "Lia Chen"
    assert made["contract"].status == Contract.Status.SENT and "Luma Skincare" in made["contract"].body
    deposit = made["invoice"]
    assert deposit.purpose == "deposit" and deposit.status == "sent" and deposit.subtotal == Decimal("1500.00")
    assert set(ServiceLine.objects.filter(workspace=ws, status="active").values_list("kind", flat=True)) == {
        "social_media",
        "branding",
    }
    assert Retainer.current_for(ws).monthly_fee == Decimal("1800.00")
    run = made["run"]
    assert run is not None
    steps = dict(run.steps.values_list("key", "status"))
    assert steps["proposal"] == "done" and steps["contract"] != "done" and steps["deposit"] != "done"

    services.sign_contract(made["contract"], studio["skincare_client"], name="Lia Chen", agreed=True, ip="203.0.113.5")
    contract = Contract.objects.get(pk=made["contract"].pk)
    assert contract.status == "signed" and len(contract.document_hash) == 64 and contract.signer_ip == "203.0.113.5"
    services.record_payment(deposit, studio["owner"], amount=deposit.balance)
    steps = dict(run.steps.values_list("key", "status"))
    assert steps["contract"] == "done" and steps["deposit"] == "done"
    assert AuditEvent.objects.filter(verb__in=["proposal.accepted", "contract.signed", "payment.recorded"]).count() == 3


def test_closed_or_expired_proposals_cannot_be_accepted(studio):
    proposal = _proposal(studio)
    Proposal.objects.filter(pk=proposal.pk).update(valid_until=_today() - timedelta(days=1))
    proposal.refresh_from_db()
    with pytest.raises(BillingError, match="expired"):
        services.accept_proposal(proposal, studio["skincare_client"], name="Lia", agreed=True)
    services.decline_proposal(proposal, studio["skincare_client"], "Budget")
    with pytest.raises(BillingError, match="no longer open"):
        services.accept_proposal(proposal, studio["skincare_client"], name="Lia", agreed=True)


def test_signing_needs_a_name_and_consent_and_happens_once(studio):
    contract = services.create_contract(studio["restaurant"], studio["owner"], send=True)
    with pytest.raises(BillingError):
        services.sign_contract(contract, studio["restaurant_client"], name="Ana", agreed=False)
    services.sign_contract(contract, studio["restaurant_client"], name="Ana Pérez", agreed=True)
    with pytest.raises(BillingError, match="not waiting"):
        services.sign_contract(contract, studio["restaurant_client"], name="Ana Pérez", agreed=True)
    with pytest.raises(BillingError, match="signed contract"):
        services.void_contract(contract, studio["owner"], "oops")


def test_template_fields_never_run_code(studio):
    assert services.fill("Hi {{ client }} {{ secret }} {% load x %}", {"client": "Ana"}) == (
        "Hi Ana {{ secret }} {% load x %}"
    )


# ── Recurring work ───────────────────────────────────────────────────────────


def test_reminders_follow_the_schedule_once_each(studio):
    Contact.objects.create(workspace=studio["restaurant"], name="Ana", email="ana@casamaya.test", is_billing=True)
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    due = invoice.due_date
    assert services.send_reminders(due - timedelta(days=4)) == 0
    assert services.send_reminders(due - timedelta(days=3)) == 1
    assert services.send_reminders(due - timedelta(days=3)) == 0  # already sent
    assert services.send_reminders(due + timedelta(days=9)) == 1  # the +7 reminder, caught up within the grace
    assert list(ReminderLog.objects.values_list("offset_days", flat=True).order_by("offset_days")) == [-3, 7]
    assert any("overdue" in m.subject for m in mail.outbox)
    services.record_payment(invoice, studio["owner"], amount=invoice.balance)
    assert services.send_reminders(due + timedelta(days=14)) == 0  # paid: nothing more


def test_retainer_drafts_once_a_month(studio):
    Retainer.objects.create(workspace=studio["restaurant"], monthly_fee=Decimal("1800"), starts_on=_today())
    first = services.draft_retainer_invoices(_today())
    again = services.draft_retainer_invoices(_today())
    assert len(first) == 1 and again == [] and first[0].status == "draft" and first[0].purpose == "retainer"


def test_overdue_sweep(studio):
    invoice = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    assert services.refresh_overdue(invoice.due_date + timedelta(days=2)) == 1
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.OVERDUE


def test_finance_summary_in_the_base_currency(studio):
    a = services.send_invoice(_invoice(studio), studio["owner"], notify=False)
    services.record_payment(a, studio["owner"], amount="125.00")
    usd = services.send_invoice(_invoice(studio, currency="USD", exchange_rate="2"), studio["owner"], notify=False)
    Retainer.objects.create(workspace=studio["restaurant"], monthly_fee=Decimal("1800"), starts_on=_today())
    summary = services.finance_summary([studio["restaurant"], studio["skincare"]])
    assert summary["outstanding"] == Decimal("1000.00") + usd.total * 2
    assert summary["collected_ytd"] == Decimal("125.00") and summary["mrr"] == Decimal("1800.00")
    assert ProposalLine.objects.count() == 0


def test_the_package_sets_the_retainer_scope(studio):
    """Posts per month come from the package sold, not from a default."""
    from apps.billing.models import CatalogItem

    package = CatalogItem.objects.create(
        organization=studio["org"], name="Social: Growth", unit="month", unit_price=Decimal("2400"), recurring=True,
        posts_per_month=16, reels_per_month=6, shoot_days_per_month=2,
    )  # fmt: skip
    ws = studio["skincare"]
    proposal = services.create_proposal(
        ws, studio["owner"], title="Growth", services=["social_media"], deposit_percent=Decimal("0"),
        start_onboarding=False, contract_template=None,
        lines=[{"description": package.name, "unit_price": package.unit_price, "recurring": True, "catalog_item": package}],
    )  # fmt: skip
    assert proposal.package() == {"posts": 16, "reels": 6, "stories": 0, "shoot_days": 2}
    services.send_proposal(proposal, studio["owner"], notify=False)
    services.accept_proposal(proposal, studio["skincare_client"], name="Lia", agreed=True)
    retainer = Retainer.current_for(ws)
    assert (retainer.posts_per_month, retainer.reels_per_month, retainer.shoot_days_per_month) == (16, 6, 2)
    assert retainer.monthly_fee == Decimal("2400.00")


def test_no_package_means_no_runway_target(studio):
    from apps.clients.health import runway_signal

    signal = runway_signal(studio["restaurant"], timezone.now(), None)
    assert signal.tone == "neutral" and "posts per month" in signal.detail
