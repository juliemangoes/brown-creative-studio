from django.urls import path

from . import portal

app_name = "billing_public"

urlpatterns = [
    path("<str:token>/", portal.public_invoice, name="invoice"),
    path("<str:token>/pdf/", portal.public_invoice_pdf, name="invoice_pdf"),
]
