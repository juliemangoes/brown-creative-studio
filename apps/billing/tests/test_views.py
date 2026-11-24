"""Billing screens: studio and portal, permissions, cross-client access, and the phase goal end to end."""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.billing import services
from apps.billing.models import Contract, Invoice, Payment, Proposal
from apps.clients.models import Contact
from apps.members.models import WorkspaceMembership
from apps.studio.permissions import client_role_permissions, ensure_client_roles
from apps.studio.tests.helpers import make_studio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    s = services.settings_for(people["org"])
    s.bank_details = "Demo Bank, account 0001"
    s.save()
    # Client users get the "Client admin" preset so they see billing in the portal.
    roles = ensure_client_roles(people["org"])
    for key in ("restaurant_client", "skincare_client"):
        WorkspaceMembership.objects.filter(user=people[key]).update(custom_role=roles["Client admin"])
    return people


def _portal(client, user, ws):
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(ws.id)
    session.save()


def _sent_invoice(studio, ws=None, amount="1000.00"):
    ws = ws or studio["restaurant"]
    inv = services.create_invoice(ws, studio["owner"], lines=[{"description": "Retainer", "unit_price": amount}])
    return services.send_invoice(inv, studio["owner"], notify=False)


# ── Studio ───────────────────────────────────────────────────────────────────


def test_owner_creates_and_sends_an_invoice(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["owner"])
    assert client.get(reverse("billing:client", kwargs={"workspace_id": ws.id})).status_code == 200
    response = client.post(
        reverse("billing:invoice_new", kwargs={"workspace_id": ws.id}), {"purpose": "one_time", "currency": "BZD"}
    )
    invoice = Invoice.objects.get(workspace=ws)
    assert response.status_code == 302
    action = reverse("billing:invoice_action", kwargs={"workspace_id": ws.id, "invoice_id": invoice.id})
    client.post(
        action,
        {"action": "add_line", "description": "Shoot day", "quantity": "1", "unit_price": "460", "tax_rate": "0"},
    )
    client.post(action, {"action": "send"})
    invoice.refresh_from_db()
    assert invoice.status == "sent" and invoice.total == Decimal("460.00") and invoice.number == "INV-0001"
    page = client.get(reverse("billing:invoice", kwargs={"workspace_id": ws.id, "invoice_id": invoice.id}))
    assert page.status_code == 200 and "INV-0001" in page.content.decode()
    pdf = client.get(reverse("billing:invoice_pdf", kwargs={"workspace_id": ws.id, "invoice_id": invoice.id}))
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")


def test_managers_without_billing_cannot_see_it(client, studio):
    """Managers have no billing by default (the brief: no billing unless granted)."""
    ws = studio["restaurant"]
    invoice = _sent_invoice(studio)
    client.force_login(studio["manager"])
    assert client.get(reverse("billing:client", kwargs={"workspace_id": ws.id})).status_code == 403
    assert (
        client.get(reverse("billing:invoice", kwargs={"workspace_id": ws.id, "invoice_id": invoice.id})).status_code
        == 403
    )
    assert client.get(reverse("billing:index")).status_code == 403
    assert (
        client.post(
            reverse("billing:invoice_action", kwargs={"workspace_id": ws.id, "invoice_id": invoice.id}),
            {"action": "void", "reason": "x"},
        ).status_code
        == 403
    )
    page = client.get(reverse("clients:detail", kwargs={"workspace_id": ws.id})).content.decode()
    assert reverse("billing:client", kwargs={"workspace_id": ws.id}) not in page


def test_finance_and_settings_need_their_studio_permissions(client, studio):
    client.force_login(studio["owner"])
    assert client.get(reverse("billing:index")).status_code == 200
    assert client.get(reverse("billing:settings")).status_code == 200
    response = client.post(
        reverse("billing:settings"),
        {"legal_name": "Brown Creative Studio Ltd", "base_currency": "BZD", "invoice_prefix": "BCS-", "credit_note_prefix": "CN-",
         "proposal_prefix": "P-", "payment_terms_days": "10", "tax_rate": "0", "reminder_text": "-2, 0, 5"},
    )  # fmt: skip
    assert response.status_code == 302
    assert services.settings_for(studio["org"]).reminder_days == [-2, 0, 5]
    client.force_login(studio["editor"])
    assert client.get(reverse("billing:settings")).status_code == 403


def test_cross_client_billing_access_is_refused(client, studio):
    luma_invoice = _sent_invoice(studio, studio["skincare"])
    luma_contract = services.create_contract(studio["skincare"], studio["owner"])
    luma_proposal = services.create_proposal(studio["skincare"], studio["owner"], title="Luma")
    client.force_login(studio["editor"])  # only on Casa Maya, and without billing
    casa = studio["restaurant"].id
    for url in (
        reverse("billing:invoice", kwargs={"workspace_id": casa, "invoice_id": luma_invoice.id}),
        reverse("billing:invoice_pdf", kwargs={"workspace_id": casa, "invoice_id": luma_invoice.id}),
        reverse("billing:contract", kwargs={"workspace_id": casa, "contract_id": luma_contract.id}),
        reverse("billing:proposal", kwargs={"workspace_id": casa, "proposal_id": luma_proposal.id}),
        reverse("billing:client", kwargs={"workspace_id": studio["skincare"].id}),
    ):
        assert client.get(url).status_code in (403, 404), url
    # An owner of both, through the wrong client's URL: not found.
    client.force_login(studio["owner"])
    sneaky = reverse("billing:invoice", kwargs={"workspace_id": casa, "invoice_id": luma_invoice.id})
    assert client.get(sneaky).status_code == 404
    action = reverse("billing:invoice_action", kwargs={"workspace_id": casa, "invoice_id": luma_invoice.id})
    assert client.post(action, {"action": "void", "reason": "x"}).status_code == 404


# ── Portal ───────────────────────────────────────────────────────────────────


def test_client_sees_only_their_own_sent_documents(client, studio):
    casa = _sent_invoice(studio)
    draft = services.create_invoice(
        studio["restaurant"], studio["owner"], lines=[{"description": "x", "unit_price": 1}]
    )
    luma = _sent_invoice(studio, studio["skincare"])
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    index = client.get(reverse("billing_portal:index")).content.decode()
    assert casa.number in index and luma.number not in index
    assert client.get(reverse("billing_portal:invoice", kwargs={"invoice_id": luma.id})).status_code == 404
    assert client.get(reverse("billing_portal:invoice", kwargs={"invoice_id": draft.id})).status_code == 404
    assert client.get(reverse("billing_portal:invoice_pdf", kwargs={"invoice_id": luma.id})).status_code == 404
    proof = reverse("billing_portal:proof", kwargs={"invoice_id": luma.id})
    assert client.post(proof, {"amount": "1", "proof": SimpleUploadedFile("r.png", PNG)}).status_code == 404


def test_internal_notes_never_reach_the_client(client, studio):
    inv = services.create_invoice(
        studio["restaurant"], studio["owner"], lines=[{"description": "Shoot", "unit_price": 100}]
    )
    inv.internal_notes = "SECRET: margin 70%"
    inv.save()
    inv = services.send_invoice(inv, studio["owner"], notify=False)
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    body = client.get(reverse("billing_portal:invoice", kwargs={"invoice_id": inv.id})).content.decode()
    assert "SECRET" not in body
    client.logout()
    public = client.get(reverse("billing_public:invoice", kwargs={"token": inv.share_token})).content.decode()
    assert "SECRET" not in public
    pdf = client.get(reverse("billing_public:invoice_pdf", kwargs={"token": inv.share_token})).content
    assert b"SECRET" not in pdf


def test_reviewers_without_billing_permission_cannot_open_billing(client, studio):
    roles = ensure_client_roles(studio["org"])
    WorkspaceMembership.objects.filter(user=studio["restaurant_client"]).update(custom_role=roles["Client reviewer"])
    assert not client_role_permissions("Client reviewer").get("portal_view_billing")
    inv = _sent_invoice(studio)
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    assert client.get(reverse("billing_portal:index")).status_code == 403
    assert client.get(reverse("billing_portal:invoice", kwargs={"invoice_id": inv.id})).status_code == 403


def test_public_link_marks_viewed_but_drafts_have_none(client, studio):
    inv = _sent_invoice(studio)
    assert client.get(reverse("billing_public:invoice", kwargs={"token": inv.share_token})).status_code == 200
    inv.refresh_from_db()
    assert inv.status == "viewed"
    draft = services.create_invoice(
        studio["restaurant"], studio["owner"], lines=[{"description": "x", "unit_price": 1}]
    )
    assert client.get(reverse("billing_public:invoice", kwargs={"token": draft.share_token})).status_code == 404


def test_mock_checkout_only_in_mock_mode(client, studio, settings):
    inv = _sent_invoice(studio)
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    settings.BILLING_PAYMENT_PROVIDER = "none"
    assert client.post(reverse("billing_portal:pay", kwargs={"invoice_id": inv.id})).status_code == 404
    assert client.get(reverse("billing_portal:mock_checkout", kwargs={"invoice_id": inv.id})).status_code == 404
    settings.BILLING_PAYMENT_PROVIDER = "mock"
    start = client.post(reverse("billing_portal:pay", kwargs={"invoice_id": inv.id}))
    assert start.status_code == 302 and "test-checkout" in start["Location"]
    page = client.get(start["Location"])
    assert "No card is charged" in page.content.decode()
    ref = start["Location"].split("ref=")[1].split("&")[0]
    client.post(reverse("billing_portal:mock_checkout", kwargs={"invoice_id": inv.id}), {"ref": ref})
    client.post(reverse("billing_portal:mock_checkout", kwargs={"invoice_id": inv.id}), {"ref": ref})  # replay
    inv.refresh_from_db()
    assert inv.status == "paid" and Payment.objects.filter(invoice=inv).count() == 1


# ── The phase goal ───────────────────────────────────────────────────────────


def test_client_signs_approves_views_an_invoice_and_pays(client, studio):
    """Phase 5 is done when a client can sign, approve, view an invoice, and record or make a payment."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.approvals import services as approvals
    from apps.studio.tests.helpers import make_account, make_post

    ws, lia = studio["skincare"], studio["skincare_client"]
    Contact.objects.create(workspace=ws, name="Lia Chen", email=lia.email, is_primary=True, user=lia)
    proposal = services.create_proposal(
        ws, studio["owner"], title="Luma social", services=["social_media"],
        lines=[{"description": "Set-up and strategy", "unit_price": Decimal("2000")},
               {"description": "Social media management", "unit_price": Decimal("1800"), "recurring": True}],
    )  # fmt: skip
    services.send_proposal(proposal, studio["owner"], notify=False)
    _portal(client, lia, ws)

    # Accept the proposal, then sign the contract it sends.
    assert client.get(reverse("billing_portal:proposal", kwargs={"proposal_id": proposal.id})).status_code == 200
    accepted = client.post(
        reverse("billing_portal:proposal_accept", kwargs={"proposal_id": proposal.id}),
        {"name": "Lia Chen", "agree": "on"},
    )
    contract = Contract.objects.get(proposal=proposal)
    assert accepted["Location"] == reverse("billing_portal:contract", kwargs={"contract_id": contract.id})
    client.post(
        reverse("billing_portal:contract_sign", kwargs={"contract_id": contract.id}),
        {"name": "Lia Chen", "agree": "on"},
    )
    contract.refresh_from_db()
    assert contract.status == "signed" and contract.signer_user == lia

    # Approve a post.
    ws.approval_workflow_mode = "required_internal_and_client"
    ws.save(update_fields=["approval_workflow_mode"])
    post = make_post(ws, make_account(ws, "Luma"), when=timezone.now() + timedelta(days=3), title="Launch teaser")
    approvals.submit_for_review(post, studio["owner"], ws)
    approvals.approve_post(post, studio["owner"], ws)
    approvals.approve_post(post, lia, ws)
    assert post.platform_posts.get().status == "approved"

    # View the deposit invoice, record a payment (receipt), the studio confirms.
    deposit = Invoice.objects.get(proposal=proposal, purpose="deposit")
    page = client.get(reverse("billing_portal:invoice", kwargs={"invoice_id": deposit.id}))
    assert page.status_code == 200 and "Demo Bank" in page.content.decode()
    deposit.refresh_from_db()
    assert deposit.status == "viewed"
    client.post(
        reverse("billing_portal:proof", kwargs={"invoice_id": deposit.id}),
        {"amount": str(deposit.balance), "proof": SimpleUploadedFile("transfer.png", PNG)},
    )
    payment = Payment.objects.get(invoice=deposit)
    assert payment.status == "pending"
    client.force_login(studio["owner"])
    client.post(
        reverse("billing:invoice_action", kwargs={"workspace_id": ws.id, "invoice_id": deposit.id}),
        {"action": "confirm_payment", "payment": str(payment.id)},
    )
    deposit.refresh_from_db()
    assert deposit.status == "paid"
    run_steps = dict(ws.onboardingruns.get().steps.values_list("key", "status"))
    assert run_steps["proposal"] == run_steps["contract"] == run_steps["deposit"] == "done"
    assert Proposal.objects.get(pk=proposal.pk).status == "accepted"


def test_finance_shows_retainer_delivery(client, studio):
    """Regression: the finance view crashed when a client had a retainer with a monthly post count."""
    from datetime import date

    from apps.clients.models import Retainer

    Retainer.objects.create(
        workspace=studio["restaurant"], monthly_fee=Decimal("1800"), posts_per_month=12, starts_on=date(2026, 1, 1)
    )
    client.force_login(studio["owner"])
    response = client.get(reverse("billing:index"))
    assert response.status_code == 200 and "of 12 posts" in response.content.decode()
