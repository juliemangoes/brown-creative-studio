from django.urls import path

from . import views

app_name = "shoots_public"

urlpatterns = [
    path("<str:token>/", views.public_call_sheet, name="call_sheet"),
    path("<str:token>/pdf/", views.public_call_sheet_pdf, name="call_sheet_pdf"),
]
