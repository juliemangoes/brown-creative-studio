from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("today/", views.today, name="today"),
    path("today/<uuid:workspace_id>/", views.today, name="today_client"),
    path("files/<str:token>/", views.private_file, name="file"),
    path("language/", views.set_language, name="set_language"),
]
