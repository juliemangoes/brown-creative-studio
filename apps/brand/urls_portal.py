from django.urls import path

from . import portal

app_name = "brand_portal"

urlpatterns = [
    path("", portal.hub, name="hub"),
    path("stages/<uuid:stage_id>/sign-off/", portal.stage_sign_off, name="sign_off"),
    path("stages/<uuid:stage_id>/changes/", portal.stage_changes, name="changes"),
    path("files/<uuid:asset_id>/", portal.download, name="download"),
]
