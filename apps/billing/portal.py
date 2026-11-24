"""Billing in the client portal: proposals to accept, contracts to sign, invoices to view and pay.

Needs the portal permission ``portal_view_billing`` (client admins and billing
contacts). Drafts, internal notes and other clients' documents are never shown.
The public share link shows one invoice read-only and counts as the client viewing it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from apps.billing import services
from apps.billing.models import Contract, Invoice, Payment, Proposal
from apps.billing.services import BillingError
from apps.studio.portal import (
    PortalNotice,
    PortalSection,
    portal_permission_required,
    portal_permissions,
    register_notice,
    register_section,
)

KEY = "portal_view_billing"


def _visible_invoices(workspace):
    return Invoice.objects.filter(workspace=workspace).exclude(status=Invoice.Status.DRAFT)


def _visible_proposals(workspace):
    return Proposal.objects.filter(workspace=workspace).exclude(status=Proposal.Status.DRAFT)


def _visible_contracts(workspace):
    return Contract.objects.filter(workspace=workspace).exclude(status=Contract.Status.DRAFT)


@register_section
def _section(request):
    ws = request.portal_workspace
    if portal_permissions(request).get(KEY) and (
        _visible_invoices(ws).exists() or _visible_proposals(ws).exists() or _visible_contracts(ws).exists()
    ):
        return PortalSection("billing_portal", gettext_lazy("Billing"), reverse("billing_portal:index"), order=60)
    return None


@register_notice
def _waiting(request):
    if not portal_permissions(request).get(KEY) or getattr(request.resolver_match, "namespace", "") == "billing_portal":
        return None
    ws = request.portal_workspace
    proposal = _visible_proposals(ws).filter(status__in=[Proposal.Status.SENT, Proposal.Status.VIEWED]).first()
    if proposal:
        return PortalNotice(
            _("Your proposal “%(title)s” is ready to review.") % {"title": proposal.title},
            reverse("billing_portal:proposal", kwargs={"proposal_id": proposal.id}),
            _("Review"),
        )
    contract = _visible_contracts(ws).filter(status__in=[Contract.Status.SENT, Contract.Status.VIEWED]).first()
    if contract:
        return PortalNotice(
            _("%(title)s is ready for your signature.") % {"title": contract.title},
            reverse("billing_portal:contract", kwargs={"contract_id": contract.id}),
            _("Sign"),
        )
    overdue = _visible_invoices(ws).filter(status=Invoice.Status.OVERDUE).first()
    if overdue:
        return PortalNotice(
            _("Invoice %(number)s is overdue.") % {"number": overdue.number},
            reverse("billing_portal:invoice", kwargs={"invoice_id": overdue.id}),
            _("Pay"),
        )
    return None


@portal_permission_required(KEY)
@require_GET
def index(request):
    ws = request.portal_workspace
    invoices = list(_visible_invoices(ws).filter(kind=Invoice.Kind.INVOICE))
    return render(
        request,
        "billing/portal/index.html",
        {
            "workspace": ws,
            "open_invoices": [i for i in invoices if i.is_open],
            "paid_invoices": [i for i in invoices if i.status == Invoice.Status.PAID],
            "credit_notes": _visible_invoices(ws).filter(kind=Invoice.Kind.CREDIT_NOTE),
            "proposals": _visible_proposals(ws),
            "contracts": _visible_contracts(ws),
            "receipts": Payment.objects.filter(workspace=ws, status=Payment.Status.CONFIRMED).select_related("invoice"),
        },
    )


def _invoice(request, invoice_id):
    return get_object_or_404(_visible_invoices(request.portal_workspace), pk=invoice_id)


@portal_permission_required(KEY)
@require_GET
def invoice(request, invoice_id):
    inv = _invoice(request, invoice_id)
    services.mark_viewed(inv, request.user)
    return render(request, "billing/portal/invoice.html", _invoice_context(inv, request=request))


def _invoice_context(inv, *, request=None, public=False):
    pay = services.payment_provider()
    return {
        "workspace": inv.workspace,
        "invoice": inv,
        "lines": inv.lines.all(),
        "taxes": services.tax_breakdown(inv),
        "payments": inv.payments.exclude(status=Payment.Status.REJECTED).order_by("created_at")
        if not public
        else inv.payments.none(),
        "rejected": inv.payments.filter(status=Payment.Status.REJECTED) if not public else [],
        "studio": services.settings_for(inv.workspace.organization),
        "online": pay if pay.online and inv.is_open and not public else None,
        "public": public,
        "tax_document": getattr(inv, "tax_document", None),
    }


@portal_permission_required(KEY)
@require_GET
def invoice_pdf(request, invoice_id):
    from apps.billing.views import pdf_response

    return pdf_response(_invoice(request, invoice_id))


def _amount(raw) -> Decimal:
    try:
        return Decimal(str(raw or "").replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise BillingError(_("Enter the amount you paid, e.g. 1500.00.")) from exc


@portal_permission_required(KEY)
@require_POST
def submit_proof(request, invoice_id):
    inv = _invoice(request, invoice_id)
    upload = request.FILES.get("proof")
    back = reverse("billing_portal:invoice", kwargs={"invoice_id": inv.id})
    if upload is None:
        messages.error(request, _("Attach the transfer receipt (a photo, screenshot or PDF)."))
        return redirect(back)
    try:
        services.submit_payment_proof(
            inv,
            request.user,
            amount=_amount(request.POST.get("amount") or inv.balance),
            upload=upload,
            reference=request.POST.get("reference", ""),
        )
    except BillingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Thank you. The studio will confirm the payment once it arrives."))
    return redirect(back)


@portal_permission_required(KEY)
@require_POST
def pay(request, invoice_id):
    inv = _invoice(request, invoice_id)
    if not services.payment_provider().online:
        raise Http404
    try:
        checkout = services.start_checkout(
            inv, request.user, return_url=request.build_absolute_uri(reverse("billing_portal:invoice", args=[inv.id]))
        )
    except BillingError as exc:
        messages.error(request, str(exc))
        return redirect("billing_portal:invoice", invoice_id=inv.id)
    return redirect(checkout.url)


@portal_permission_required(KEY)
def mock_checkout(request, invoice_id):
    """The mock provider's checkout page. It says plainly that no money moves."""
    inv = _invoice(request, invoice_id)
    pay_provider = services.payment_provider()
    if pay_provider.key != "mock":
        raise Http404
    reference = request.GET.get("ref") or request.POST.get("ref", "")
    if request.method == "POST":
        if not reference.startswith("mock_"):
            raise Http404
        try:
            services.complete_online_payment(
                inv, request.user, reference=reference, amount=inv.balance, provider_key="mock"
            )
        except BillingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Test payment recorded. No money moved."))
        return redirect("billing_portal:invoice", invoice_id=inv.id)
    return render(request, "billing/portal/mock_checkout.html", {"invoice": inv, "reference": reference})


# ── Proposals and contracts ──────────────────────────────────────────────────


def _proposal(request, proposal_id):
    return get_object_or_404(_visible_proposals(request.portal_workspace), pk=proposal_id)


@portal_permission_required(KEY)
@require_GET
def proposal(request, proposal_id):
    p = _proposal(request, proposal_id)
    services.mark_proposal_viewed(p, request.user)
    from apps.clients.models import ServiceLine

    return render(
        request,
        "billing/portal/proposal.html",
        {
            "workspace": p.workspace,
            "proposal": p,
            "lines": p.lines.all(),
            "service_labels": dict(ServiceLine.Kind.choices),
        },
    )


@portal_permission_required(KEY)
@require_POST
def proposal_accept(request, proposal_id):
    p = _proposal(request, proposal_id)
    try:
        made = services.accept_proposal(
            p, request.user, name=request.POST.get("name", ""), agreed=request.POST.get("agree") == "on"
        )
    except BillingError as exc:
        messages.error(request, str(exc))
        return redirect("billing_portal:proposal", proposal_id=p.id)
    messages.success(request, _("Thank you for accepting. Here is what happens next."))
    if made["contract"]:
        return redirect("billing_portal:contract", contract_id=made["contract"].id)
    return redirect("billing_portal:index")


@portal_permission_required(KEY)
@require_POST
def proposal_decline(request, proposal_id):
    p = _proposal(request, proposal_id)
    try:
        services.decline_proposal(p, request.user, request.POST.get("reason", "")[:2000])
    except BillingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("We've let the studio know."))
    return redirect("billing_portal:index")


def _contract(request, contract_id):
    return get_object_or_404(_visible_contracts(request.portal_workspace), pk=contract_id)


@portal_permission_required(KEY)
@require_GET
def contract(request, contract_id):
    c = _contract(request, contract_id)
    services.mark_contract_viewed(c, request.user)
    deposit = None
    if c.proposal_id:
        deposit = (
            _visible_invoices(c.workspace).filter(proposal_id=c.proposal_id, purpose=Invoice.Purpose.DEPOSIT).first()
        )
    return render(
        request, "billing/portal/contract.html", {"workspace": c.workspace, "contract": c, "deposit": deposit}
    )


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


@portal_permission_required(KEY)
@require_POST
def contract_sign(request, contract_id):
    c = _contract(request, contract_id)
    try:
        services.sign_contract(
            c,
            request.user,
            name=request.POST.get("name", ""),
            agreed=request.POST.get("agree") == "on",
            ip=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
    except BillingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Signed. A copy stays here in Billing."))
    return redirect("billing_portal:contract", contract_id=c.id)


# ── Public share link (for billing contacts without a portal login) ──────────


def _public(token):
    inv = Invoice.objects.filter(share_token=token).exclude(status=Invoice.Status.DRAFT).first()
    if inv is None:
        raise Http404
    return inv


@require_GET
def public_invoice(request, token):
    inv = _public(token)
    services.mark_viewed(inv, None)
    response = render(request, "billing/public_invoice.html", _invoice_context(inv, public=True))
    response["X-Robots-Tag"] = "noindex"
    return response


@require_GET
def public_invoice_pdf(request, token):
    from apps.billing.views import pdf_response

    return pdf_response(_public(token))
