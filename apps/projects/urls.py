from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.all_tasks, name="all"),
    path("workload/", views.workload_view, name="workload"),
    path("templates/", views.template_list, name="templates"),
    path("templates/new/", views.template_edit, name="template_new"),
    path("templates/<uuid:template_id>/", views.template_edit, name="template_edit"),
    path("filters/save/", views.save_filter, name="save_filter"),
    path("filters/<uuid:filter_id>/delete/", views.delete_filter, name="delete_filter"),
    path("<uuid:workspace_id>/", views.client_projects, name="list"),
    path("<uuid:workspace_id>/new/", views.project_create, name="create"),
    path("<uuid:workspace_id>/p/<uuid:project_id>/", views.project_detail, name="project"),
    path("<uuid:workspace_id>/p/<uuid:project_id>/edit/", views.project_update, name="project_update"),
    path("<uuid:workspace_id>/p/<uuid:project_id>/tasks/new/", views.task_create, name="task_create"),
    path("<uuid:workspace_id>/t/<uuid:task_id>/", views.task_detail, name="task"),
    path("<uuid:workspace_id>/t/<uuid:task_id>/status/", views.task_status, name="task_status"),
    path("<uuid:workspace_id>/t/<uuid:task_id>/checklist/", views.checklist_add, name="checklist_add"),
    path(
        "<uuid:workspace_id>/t/<uuid:task_id>/checklist/<uuid:item_id>/",
        views.checklist_toggle,
        name="checklist_toggle",
    ),
    path("<uuid:workspace_id>/t/<uuid:task_id>/comments/", views.comment_add, name="comment_add"),
    path("<uuid:workspace_id>/t/<uuid:task_id>/links/", views.link_add, name="link_add"),
    path("<uuid:workspace_id>/t/<uuid:task_id>/delete/", views.task_delete, name="task_delete"),
]
