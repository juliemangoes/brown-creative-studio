from django.urls import path

from . import portal

app_name = "planning_portal"

urlpatterns = [path("", portal.calendar, name="calendar")]
