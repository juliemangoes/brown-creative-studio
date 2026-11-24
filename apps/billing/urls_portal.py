from django.urls import path

from . import portal

app_name = "billing_portal"

urlpatterns = [
    path("", portal.index, name="index"),
    path("invoices/<uuid:invoice_id>/", portal.invoice, name="invoice"),
    path("invoices/<uuid:invoice_id>/pdf/", portal.invoice_pdf, name="invoice_pdf"),
    path("invoices/<uuid:invoice_id>/proof/", portal.submit_proof, name="proof"),
    path("invoices/<uuid:invoice_id>/pay/", portal.pay, name="pay"),
    path("invoices/<uuid:invoice_id>/test-checkout/", portal.mock_checkout, name="mock_checkout"),
    path("proposals/<uuid:proposal_id>/", portal.proposal, name="proposal"),
    path("proposals/<uuid:proposal_id>/accept/", portal.proposal_accept, name="proposal_accept"),
    path("proposals/<uuid:proposal_id>/decline/", portal.proposal_decline, name="proposal_decline"),
    path("contracts/<uuid:contract_id>/", portal.contract, name="contract"),
    path("contracts/<uuid:contract_id>/sign/", portal.contract_sign, name="contract_sign"),
]
