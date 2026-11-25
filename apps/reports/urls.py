from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.all_reports, name="all"),
    path("<uuid:workspace_id>/", views.dashboard, name="dashboard"),
    path("<uuid:workspace_id>/goals/", views.goals, name="goals"),
    path("<uuid:workspace_id>/goals/<uuid:goal_id>/toggle/", views.goal_toggle, name="goal_toggle"),
    path("<uuid:workspace_id>/reports/", views.report_list, name="list"),
    path("<uuid:workspace_id>/reports/new/", views.report_create, name="create"),
    path("<uuid:workspace_id>/reports/<uuid:report_id>/", views.report_detail, name="report"),
    path("<uuid:workspace_id>/reports/<uuid:report_id>/pdf/", views.report_pdf, name="pdf"),
    path("<uuid:workspace_id>/reports/<uuid:report_id>/<slug:action>/", views.report_action, name="action"),
    path("<uuid:workspace_id>/import/", views.import_csv, name="import"),
]
