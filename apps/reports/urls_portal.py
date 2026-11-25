from django.urls import path

from . import portal

app_name = "reports_portal"

urlpatterns = [
    path("", portal.index, name="index"),
    path("<uuid:report_id>/", portal.report, name="report"),
    path("<uuid:report_id>/pdf/", portal.report_pdf, name="pdf"),
]
