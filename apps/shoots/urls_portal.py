from django.urls import path

from . import portal

app_name = "shoots_portal"

urlpatterns = [
    path("", portal.index, name="index"),
    path("<uuid:shoot_id>/checklist/", portal.prep, name="prep"),
]
