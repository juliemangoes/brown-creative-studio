"""Studio billing: each client's proposals, contracts and invoices; the finance view; settings and catalog.

Seeing billing needs ``view_billing`` on the client; changing anything needs
``manage_billing``. The finance view needs the studio-wide ``view_finance`` and
settings need ``manage_billing_settings``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.billing import services
from apps.billing.adapters import provider
from apps.billing.models import (
    BillingSettings,
    CatalogItem,
    Contract,
    DocumentTemplate,
    Invoice,
    Payment,
    Proposal,
    ProposalLine,
)
from apps.billing.services import BillingError
from apps.clients.forms import _style
from apps.clients.models import ServiceLine
from apps.members.decorators import require_permission
from apps.studio.decorators import in_local_time, in_studio_time, studio_staff_required
from apps.studio.permissions import has_studio_permission, workspace_permission
from apps.studio.today import staff_memberships


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value or default).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise BillingError(f"“{value}” is not a number.") from exc


def _can_manage(request) -> bool:
    return workspace_permission(request, "manage_billing")


def _invoice(request, invoice_id):
    return get_object_or_404(Invoice.objects.filter(workspace=request.workspace), pk=invoice_id)


def _proposal(request, proposal_id):
    return get_object_or_404(Proposal.objects.filter(workspace=request.workspace), pk=proposal_id)


def _contract(request, contract_id):
    return get_object_or_404(Contract.objects.filter(workspace=request.workspace), pk=contract_id)


def _run(request, fn, success, redirect_to):
    """Run a billing action; show its error instead of failing the request."""
    try:
        result = fn()
    except BillingError as exc:
        messages.error(request, str(exc))
    else:
        if success:
            messages.success(request, success(result) if callable(success) else success)
    return redirect(redirect_to)


# ── All clients: finance ─────────────────────────────────────────────────────


@studio_staff_required
@in_studio_time
def index(request):
    if not has_studio_permission(request.org_membership, "view_finance"):
        raise PermissionDenied("Permission denied: view_finance")
    workspaces = [
        m.workspace for m in staff_memberships(request.user, request.org) if m.effective_permissions.get("view_billing")
    ]
    ids = [w.id for w in workspaces]
    status = request.GET.get("status", "open")
    invoices = Invoice.objects.filter(workspace_id__in=ids).select_related("workspace")
    if status == "open":
        invoices = invoices.filter(status__in=Invoice.OPEN_STATUSES)
    elif status in Invoice.Status.values:
        invoices = invoices.filter(status=status)
    pending = Payment.objects.filter(workspace_id__in=ids, status=Payment.Status.PENDING).select_related(
        "invoice", "workspace"
    )
    from apps.clients.models import Retainer

    delivery = []
    for ws in workspaces:
        retainer = Retainer.current_for(ws)
        if retainer and retainer.posts_per_month:
            from apps.projects.services import retainer_delivery

            delivery.append((ws, retainer_delivery(ws)))
    return render(
        request,
        "billing/index.html",
        {
            "summary": services.finance_summary(workspaces),
            "invoices": invoices.order_by("due_date", "-created_at")[:100],
            "pending": pending,
            "status": status,
            "statuses": [("open", "Open"), *Invoice.Status.choices],
            "base_currency": services.settings_for(request.org).base_currency,
            "delivery": delivery,
            "service_labels": dict(ServiceLine.Kind.choices),
        },
    )


# ── One client ───────────────────────────────────────────────────────────────


@require_permission("view_billing")
@in_local_time
def client(request, workspace_id):
    from apps.clients.views import _context as client_context

    ws = request.workspace
    ctx = client_context(request, "billing")
    return render(
        request,
        "billing/client.html",
        {
            **ctx,
            "workspace": ws,
            "invoices": Invoice.objects.filter(workspace=ws).prefetch_related("payments"),
            "proposals": Proposal.objects.filter(workspace=ws).prefetch_related("lines"),
            "contracts": Contract.objects.filter(workspace=ws),
            "pending": Payment.objects.filter(workspace=ws, status=Payment.Status.PENDING).select_related("invoice"),
            "can_manage": _can_manage(request),
            "currencies": _currencies(request),
            "purposes": Invoice.Purpose.choices,
            "contract_templates": DocumentTemplate.objects.filter(
                organization=request.org, kind=DocumentTemplate.Kind.CONTRACT
            ),
        },
    )


# ── Invoices ─────────────────────────────────────────────────────────────────


class InvoiceForm(forms.Form):
    purpose = forms.ChoiceField(choices=Invoice.Purpose.choices)
    title = forms.CharField(max_length=200, required=False)
    currency = forms.ChoiceField(choices=[])
    exchange_rate = forms.DecimalField(
        max_digits=14, decimal_places=6, required=False, help_text="Only for another currency than the studio's."
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Note to the client")

    def __init__(self, *args, currencies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].choices = [(c, c) for c in currencies]
        _style(self)


def _currencies(request):
    from django.conf import settings

    base = services.settings_for(request.org).base_currency
    return list(dict.fromkeys([base, *settings.BILLING_CURRENCIES]))


@require_permission("manage_billing")
@require_POST
def invoice_new(request, workspace_id):
    form = InvoiceForm(request.POST, currencies=_currencies(request))
    if not form.is_valid():
        messages.error(request, "Check the invoice details.")
        return redirect("billing:client", workspace_id=workspace_id)
    data = form.cleaned_data
    try:
        invoice = services.create_invoice(
            request.workspace,
            request.user,
            purpose=data["purpose"],
            title=data["title"],
            currency=data["currency"],
            exchange_rate=data["exchange_rate"],
            notes=data["notes"],
        )
    except BillingError as exc:
        messages.error(request, str(exc))
        return redirect("billing:client", workspace_id=workspace_id)
    return redirect("billing:invoice", workspace_id=workspace_id, invoice_id=invoice.id)


@require_permission("view_billing")
@in_local_time
def invoice_detail(request, workspace_id, invoice_id):
    invoice = _invoice(request, invoice_id)
    return render(
        request,
        "billing/invoice.html",
        {
            "workspace": request.workspace,
            "invoice": invoice,
            "lines": invoice.lines.all(),
            "payments": invoice.payments.prefetch_related("proofs"),
            "credit_notes": invoice.credit_notes.all(),
            "taxes": services.tax_breakdown(invoice),
            "catalog": CatalogItem.objects.filter(organization=request.org, is_active=True),
            "can_manage": _can_manage(request),
            "editable": _can_manage(request) and invoice.status == Invoice.Status.DRAFT,
            "methods": Payment.Method.choices,
            "today": timezone.localdate(),
            "tax_provider": provider("tax"),
            "share_url": services.share_url(invoice),
            "studio": services.settings_for(request.org),
            "service_kinds": ServiceLine.Kind.choices,
        },
    )


@require_permission("manage_billing")
@require_POST
def invoice_action(request, workspace_id, invoice_id):
    invoice = _invoice(request, invoice_id)
    action = request.POST.get("action", "")
    back = reverse("billing:invoice", kwargs={"workspace_id": workspace_id, "invoice_id": invoice.id})
    post, user = request.POST, request.user

    def add_line():
        item = CatalogItem.objects.filter(organization=request.org, pk=post.get("catalog_item") or None).first()
        services.add_line(
            invoice,
            description=post.get("description", "").strip() or (item.name if item else ""),
            quantity=_dec(post.get("quantity"), "1"),
            unit_price=_dec(post.get("unit_price") or (item.unit_price if item else "0")),
            discount_percent=_dec(post.get("discount_percent"), "0"),
            tax_rate=_dec(post["tax_rate"]) if post.get("tax_rate", "") != "" else None,
            service_kind=post.get("service_kind", "") or (item.service_kind if item else ""),
            catalog_item=item,
        )
        services.recalculate(invoice)

    def remove_line():
        services._require_draft(invoice)
        invoice.lines.filter(pk=post.get("line")).delete()
        services.recalculate(invoice)

    def update_draft():
        services._require_draft(invoice)
        invoice.discount_amount = _dec(post.get("discount_amount"), "0")
        invoice.notes = post.get("notes", "")
        invoice.internal_notes = post.get("internal_notes", "")
        if post.get("due_date"):
            invoice.due_date = post["due_date"]
        services.recalculate(invoice)

    actions = {
        "add_line": (add_line, "Line added."),
        "remove_line": (remove_line, "Line removed."),
        "update": (update_draft, "Draft saved."),
        "send": (lambda: services.send_invoice(invoice, user), lambda inv: f"{inv.number} issued and sent."),
        "record_payment": (
            lambda: services.record_payment(
                invoice,
                user,
                amount=_dec(post.get("amount")),
                method=post.get("method", Payment.Method.BANK_TRANSFER),
                received_on=post.get("received_on") or None,
                reference=post.get("reference", ""),
                note=post.get("note", ""),
                proof=request.FILES.get("proof"),
            ),
            "Payment recorded.",
        ),
        "confirm_payment": (
            lambda: services.confirm_payment(get_object_or_404(invoice.payments, pk=post.get("payment")), user),
            "Payment confirmed.",
        ),
        "reject_payment": (
            lambda: services.reject_payment(
                get_object_or_404(invoice.payments, pk=post.get("payment")), user, post.get("reason", "")
            ),
            "Marked as not received. The client sees why.",
        ),
        "void": (lambda: services.void_invoice(invoice, user, post.get("reason", "")), "Invoice voided."),
        "credit": (
            lambda: services.create_credit_note(
                invoice, user, amount=_dec(post.get("amount")), reason=post.get("reason", "")
            ),
            None,
        ),
        "tax_document": (lambda: services.request_tax_document(invoice, user), "Tax document requested."),
    }
    if action not in actions:
        messages.error(request, "Unknown action.")
        return redirect(back)
    fn, success = actions[action]
    if action == "credit":
        try:
            note = fn()
        except BillingError as exc:
            messages.error(request, str(exc))
            return redirect(back)
        messages.success(request, "Draft credit note created. Check it and send it.")
        return redirect("billing:invoice", workspace_id=workspace_id, invoice_id=note.id)
    return _run(request, fn, success, back)


@require_permission("manage_billing")
@require_POST
def invoice_delete(request, workspace_id, invoice_id):
    invoice = _invoice(request, invoice_id)
    if invoice.status != Invoice.Status.DRAFT:
        messages.error(request, "Only drafts can be deleted. Void or credit a sent invoice instead.")
        return redirect("billing:invoice", workspace_id=workspace_id, invoice_id=invoice.id)
    invoice.delete()
    messages.success(request, "Draft deleted.")
    return redirect("billing:client", workspace_id=workspace_id)


@require_permission("view_billing")
def invoice_pdf(request, workspace_id, invoice_id):
    return pdf_response(_invoice(request, invoice_id))


def pdf_response(invoice, *, inline=False):
    from apps.billing.pdf import render_invoice

    response = HttpResponse(render_invoice(invoice), content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{invoice.number or "draft"}.pdf"'
    return response


# ── Proposals ────────────────────────────────────────────────────────────────


class ProposalForm(forms.ModelForm):
    services = forms.MultipleChoiceField(
        choices=ServiceLine.Kind.choices, widget=forms.CheckboxSelectMultiple, required=False
    )

    class Meta:
        model = Proposal
        fields = ["title", "introduction", "currency", "valid_until", "deposit_percent", "contract_template",
                  "start_onboarding"]  # fmt: skip
        widgets = {
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "introduction": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {"start_onboarding": "Start onboarding when accepted (new clients)"}

    def __init__(self, *args, organization=None, currencies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"] = forms.ChoiceField(choices=[(c, c) for c in currencies])
        self.fields["contract_template"].queryset = DocumentTemplate.objects.filter(
            organization=organization, kind=DocumentTemplate.Kind.CONTRACT
        )
        self.fields["contract_template"].required = False
        self.fields["contract_template"].label = "Contract to send on acceptance"
        if self.instance.pk:
            self.initial["services"] = self.instance.services
        _style(self)


@require_permission("manage_billing")
@require_POST
def proposal_new(request, workspace_id):
    proposal = services.create_proposal(
        request.workspace, request.user, title=request.POST.get("title", "").strip() or "Proposal"
    )
    return redirect("billing:proposal", workspace_id=workspace_id, proposal_id=proposal.id)


@require_permission("view_billing")
@in_local_time
@require_http_methods(["GET", "POST"])
def proposal_detail(request, workspace_id, proposal_id):
    proposal = _proposal(request, proposal_id)
    editable = proposal.status == Proposal.Status.DRAFT and _can_manage(request)
    form = ProposalForm(
        request.POST or None, instance=proposal, organization=request.org, currencies=_currencies(request)
    )
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            saved = form.save(commit=False)
            saved.services = form.cleaned_data["services"]
            saved.save()
            messages.success(request, "Proposal saved.")
            return redirect("billing:proposal", workspace_id=workspace_id, proposal_id=proposal.id)
    return render(
        request,
        "billing/proposal.html",
        {
            "workspace": request.workspace,
            "proposal": proposal,
            "form": form,
            "lines": proposal.lines.all(),
            "editable": editable,
            "can_manage": _can_manage(request),
            "catalog": CatalogItem.objects.filter(organization=request.org, is_active=True),
            "service_labels": dict(ServiceLine.Kind.choices),
        },
    )


@require_permission("manage_billing")
@require_POST
def proposal_action(request, workspace_id, proposal_id):
    proposal = _proposal(request, proposal_id)
    action, post = request.POST.get("action", ""), request.POST
    back = reverse("billing:proposal", kwargs={"workspace_id": workspace_id, "proposal_id": proposal.id})

    def add_line():
        if proposal.status != Proposal.Status.DRAFT:
            raise BillingError("Only a draft proposal can be changed.")
        item = CatalogItem.objects.filter(organization=request.org, pk=post.get("catalog_item") or None).first()
        ProposalLine.objects.create(
            proposal=proposal,
            catalog_item=item,
            description=(post.get("description", "").strip() or (item.name if item else "Service"))[:300],
            quantity=_dec(post.get("quantity"), "1"),
            unit_price=_dec(post.get("unit_price") or (item.unit_price if item else "0")),
            discount_percent=_dec(post.get("discount_percent"), "0"),
            recurring=bool(post.get("recurring")) or bool(item and item.recurring),
            position=proposal.lines.count(),
        )

    def remove_line():
        if proposal.status != Proposal.Status.DRAFT:
            raise BillingError("Only a draft proposal can be changed.")
        proposal.lines.filter(pk=post.get("line")).delete()

    def withdraw():
        if not proposal.is_open:
            raise BillingError("Only an open proposal can be withdrawn.")
        proposal.status = Proposal.Status.WITHDRAWN
        proposal.save(update_fields=["status", "updated_at"])
        services.record("proposal.withdrawn", f"Proposal {proposal.number} withdrawn", workspace=proposal.workspace,
                        actor=request.user, target=proposal)  # fmt: skip

    actions = {
        "add_line": (add_line, "Line added."),
        "remove_line": (remove_line, "Line removed."),
        "send": (lambda: services.send_proposal(proposal, request.user), "Proposal sent to the client."),
        "withdraw": (withdraw, "Proposal withdrawn."),
    }
    if action not in actions:
        messages.error(request, "Unknown action.")
        return redirect(back)
    fn, success = actions[action]
    return _run(request, fn, success, back)


# ── Contracts ────────────────────────────────────────────────────────────────


@require_permission("manage_billing")
@require_POST
def contract_new(request, workspace_id):
    template = DocumentTemplate.objects.filter(
        organization=request.org, kind=DocumentTemplate.Kind.CONTRACT, pk=request.POST.get("template") or None
    ).first()
    contract = services.create_contract(request.workspace, request.user, template=template)
    return redirect("billing:contract", workspace_id=workspace_id, contract_id=contract.id)


@require_permission("view_billing")
@in_local_time
@require_http_methods(["GET", "POST"])
def contract_detail(request, workspace_id, contract_id):
    contract = _contract(request, contract_id)
    editable = contract.status == Contract.Status.DRAFT and _can_manage(request)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        contract.title = request.POST.get("title", contract.title).strip()[:200] or contract.title
        contract.body = request.POST.get("body", contract.body)
        contract.save(update_fields=["title", "body", "updated_at"])
        messages.success(request, "Contract saved.")
        return redirect("billing:contract", workspace_id=workspace_id, contract_id=contract.id)
    return render(
        request,
        "billing/contract.html",
        {
            "workspace": request.workspace,
            "contract": contract,
            "editable": editable,
            "can_manage": _can_manage(request),
        },
    )


@require_permission("manage_billing")
@require_POST
def contract_action(request, workspace_id, contract_id):
    contract = _contract(request, contract_id)
    action = request.POST.get("action", "")
    back = reverse("billing:contract", kwargs={"workspace_id": workspace_id, "contract_id": contract.id})
    actions = {
        "send": (lambda: services.send_contract(contract, request.user), "Contract sent for signature."),
        "void": (
            lambda: services.void_contract(contract, request.user, request.POST.get("reason", "")),
            "Contract voided.",
        ),
    }
    if action not in actions:
        messages.error(request, "Unknown action.")
        return redirect(back)
    fn, success = actions[action]
    return _run(request, fn, success, back)


# ── Studio settings and catalog ──────────────────────────────────────────────


class SettingsForm(forms.ModelForm):
    reminder_text = forms.CharField(
        required=False,
        label="Reminder schedule",
        help_text="Days relative to the due date, separated by commas: -3 is three days before, 7 a week after.",
    )

    class Meta:
        model = BillingSettings
        fields = ["legal_name", "address", "tax_id", "email", "phone", "base_currency", "bank_details",
                  "payment_instructions", "invoice_prefix", "credit_note_prefix", "proposal_prefix",
                  "payment_terms_days", "tax_name", "tax_rate", "footer_note"]  # fmt: skip
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "bank_details": forms.Textarea(attrs={"rows": 3}),
            "payment_instructions": forms.Textarea(attrs={"rows": 2}),
            "footer_note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["reminder_text"] = ", ".join(str(d) for d in self.instance.reminder_days or [])
        _style(self)

    def clean_reminder_text(self):
        raw = self.cleaned_data.get("reminder_text", "")
        try:
            days = sorted({int(x) for x in raw.replace(" ", "").split(",") if x})
        except ValueError as exc:
            raise forms.ValidationError("Use whole numbers separated by commas, e.g. -3, 0, 7, 14.") from exc
        if any(abs(d) > 120 for d in days):
            raise forms.ValidationError("Keep reminders within 120 days of the due date.")
        return days


@studio_staff_required
@require_http_methods(["GET", "POST"])
def billing_settings(request):
    if not has_studio_permission(request.org_membership, "manage_billing_settings"):
        raise PermissionDenied("Permission denied: manage_billing_settings")
    obj = services.settings_for(request.org)
    form = SettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.reminder_days = form.cleaned_data["reminder_text"]
        saved.save()
        services.record("billing.settings_changed", "Invoice details and billing settings updated",
                        organization=request.org, actor=request.user, target=saved)  # fmt: skip
        messages.success(request, "Billing settings saved.")
        return redirect("billing:settings")
    templates = services.ensure_templates(request.org)
    return render(
        request,
        "billing/settings.html",
        {
            "form": form,
            "providers": {k: provider(k) for k in ("payment", "signature", "tax")},
            "templates": DocumentTemplate.objects.filter(organization=request.org),
            "builtin": templates,
        },
    )


class CatalogForm(forms.ModelForm):
    class Meta:
        model = CatalogItem
        fields = ["name", "description", "service_kind", "unit", "unit_price", "currency", "recurring", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service_kind"] = forms.ChoiceField(
            choices=[("", "None"), *ServiceLine.Kind.choices], required=False, label="Service line"
        )
        _style(self)


@studio_staff_required
@require_http_methods(["GET", "POST"])
def catalog(request, item_id=None):
    if not has_studio_permission(request.org_membership, "manage_billing_settings"):
        raise PermissionDenied("Permission denied: manage_billing_settings")
    item = get_object_or_404(CatalogItem, organization=request.org, pk=item_id) if item_id else None
    form = CatalogForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.organization = request.org
        saved.save()
        messages.success(request, f"{saved.name} saved.")
        return redirect("billing:catalog")
    return render(
        request,
        "billing/catalog.html",
        {"form": form, "item": item, "items": CatalogItem.objects.filter(organization=request.org)},
    )


@studio_staff_required
@require_http_methods(["GET", "POST"])
def template_edit(request, template_id):
    if not has_studio_permission(request.org_membership, "manage_templates"):
        raise PermissionDenied("Permission denied: manage_templates")
    template = get_object_or_404(DocumentTemplate, organization=request.org, pk=template_id)
    if request.method == "POST":
        template.name = request.POST.get("name", template.name).strip()[:160] or template.name
        template.body = request.POST.get("body", template.body)
        template.save(update_fields=["name", "body", "updated_at"])
        messages.success(request, "Template saved. New proposals and contracts use it; sent ones keep their text.")
        return redirect("billing:settings")
    return render(request, "billing/template_edit.html", {"template": template, "fields": sorted(_TEMPLATE_FIELDS)})


_TEMPLATE_FIELDS = {"studio", "client", "date", "proposal", "services", "currency", "one_off_total", "monthly_total",
                    "deposit", "payment_terms"}  # fmt: skip
