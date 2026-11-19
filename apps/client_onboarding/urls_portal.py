from django.urls import path

from . import portal

app_name = "onboarding_portal"

urlpatterns = [
    path("", portal.overview, name="overview"),
    path("step/<str:key>/", portal.step_view, name="step"),
    path("upload/<str:category>/", portal.upload, name="upload"),
]
