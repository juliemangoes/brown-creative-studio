from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("<uuid:workspace_id>/<uuid:asset_id>/rights/", views.rights, name="rights"),
]
