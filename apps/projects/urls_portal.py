from django.urls import path

from . import portal

app_name = "projects_portal"

urlpatterns = [
    path("", portal.project_list, name="list"),
]
