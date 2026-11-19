from django.urls import path

from . import views

app_name = "client_onboarding"

urlpatterns = [
    path("templates/", views.template_list, name="templates"),
    path("templates/<uuid:template_id>/", views.template_edit, name="template_edit"),
    path("<uuid:workspace_id>/", views.run_detail, name="run"),
    path("<uuid:workspace_id>/start/", views.run_start, name="start"),
    path("<uuid:workspace_id>/steps/<uuid:step_id>/", views.step_action, name="step_action"),
    path("<uuid:workspace_id>/reminders/", views.reminders_save, name="reminders"),
    path("<uuid:workspace_id>/remind/", views.remind_now, name="remind"),
    path("<uuid:workspace_id>/invite/", views.invite_contact, name="invite"),
    path("<uuid:workspace_id>/kickoff/", views.kickoff_slots, name="kickoff"),
    path("<uuid:workspace_id>/cancel/", views.run_cancel, name="cancel"),
    path("<uuid:workspace_id>/complete/", views.run_complete, name="complete"),
]
