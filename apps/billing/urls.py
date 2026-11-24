from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.index, name="index"),
    path("settings/", views.billing_settings, name="settings"),
    path("settings/catalog/", views.catalog, name="catalog"),
    path("settings/catalog/<uuid:item_id>/", views.catalog, name="catalog_item"),
    path("settings/templates/<uuid:template_id>/", views.template_edit, name="template"),
    path("<uuid:workspace_id>/", views.client, name="client"),
    path("<uuid:workspace_id>/invoices/new/", views.invoice_new, name="invoice_new"),
    path("<uuid:workspace_id>/invoices/<uuid:invoice_id>/", views.invoice_detail, name="invoice"),
    path("<uuid:workspace_id>/invoices/<uuid:invoice_id>/action/", views.invoice_action, name="invoice_action"),
    path("<uuid:workspace_id>/invoices/<uuid:invoice_id>/delete/", views.invoice_delete, name="invoice_delete"),
    path("<uuid:workspace_id>/invoices/<uuid:invoice_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("<uuid:workspace_id>/proposals/new/", views.proposal_new, name="proposal_new"),
    path("<uuid:workspace_id>/proposals/<uuid:proposal_id>/", views.proposal_detail, name="proposal"),
    path("<uuid:workspace_id>/proposals/<uuid:proposal_id>/action/", views.proposal_action, name="proposal_action"),
    path("<uuid:workspace_id>/contracts/new/", views.contract_new, name="contract_new"),
    path("<uuid:workspace_id>/contracts/<uuid:contract_id>/", views.contract_detail, name="contract"),
    path("<uuid:workspace_id>/contracts/<uuid:contract_id>/action/", views.contract_action, name="contract_action"),
]
