from django.urls import path

from . import views

app_name = "publishing"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("<uuid:workspace_id>/", views.client_queue, name="client"),
    path("<uuid:workspace_id>/by-hand/<uuid:task_id>/", views.task_detail, name="task"),
    path(
        "<uuid:workspace_id>/by-hand/<uuid:task_id>/file/<uuid:attachment_id>/",
        views.task_download,
        name="task_download",
    ),
    path("<uuid:workspace_id>/by-hand/<uuid:task_id>/done/", views.task_complete, name="task_complete"),
    path("<uuid:workspace_id>/by-hand/<uuid:task_id>/assign/", views.task_reassign, name="task_reassign"),
    path("<uuid:workspace_id>/posts/<uuid:platform_post_id>/retry/", views.retry, name="retry"),
    path("<uuid:workspace_id>/posts/<uuid:platform_post_id>/live/", views.mark_live, name="mark_live"),
]
