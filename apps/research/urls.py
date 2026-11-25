from django.urls import path

from . import views

app_name = "research"

urlpatterns = [
    path("", views.all_clients, name="all"),
    path("<uuid:workspace_id>/", views.index, name="index"),
    path("<uuid:workspace_id>/generate/", views.generate, name="generate"),
    path("<uuid:workspace_id>/briefs/<uuid:brief_id>/", views.brief_detail, name="brief"),
    path("<uuid:workspace_id>/items/<uuid:item_id>/<slug:action>/", views.item_action, name="item_action"),
    path("<uuid:workspace_id>/profile/", views.profile, name="profile"),
    path("<uuid:workspace_id>/swipe/", views.swipe, name="swipe"),
    path("<uuid:workspace_id>/swipe/<uuid:item_id>/delete/", views.swipe_delete, name="swipe_delete"),
]
