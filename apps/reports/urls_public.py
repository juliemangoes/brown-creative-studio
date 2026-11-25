from django.urls import path

from . import portal

app_name = "reports_public"

urlpatterns = [
    path("<str:token>/", portal.public_report, name="report"),
    path("<str:token>/pdf/", portal.public_pdf, name="pdf"),
]
