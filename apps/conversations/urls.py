from django.urls import path

from . import views

app_name = "conversations"

urlpatterns = [
    path("<uuid:workspace_id>/", views.client_messages, name="client"),
    path("<uuid:workspace_id>/new/", views.start, name="start"),
    path("<uuid:workspace_id>/<uuid:conversation_id>/", views.client_messages, name="thread"),
    path("<uuid:workspace_id>/<uuid:conversation_id>/reply/", views.reply, name="reply"),
]
