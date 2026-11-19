from django.urls import path

from . import views

app_name = "design_system"

urlpatterns = [
    path("design-system/", views.style_guide, name="style_guide"),
]
