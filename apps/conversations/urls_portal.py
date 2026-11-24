from django.urls import path

from . import portal

app_name = "conversations_portal"

urlpatterns = [
    path("", portal.index, name="index"),
    path("<uuid:conversation_id>/", portal.thread, name="thread"),
    path("<uuid:conversation_id>/reply/", portal.reply, name="reply"),
]
