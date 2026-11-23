from django.urls import path

from . import views

app_name = "shoots"

urlpatterns = [
    path("", views.all_shoots, name="all"),
    path("sw.js", views.service_worker, name="sw"),
    path("<uuid:workspace_id>/", views.client_shoots, name="list"),
    path("<uuid:workspace_id>/new/", views.shoot_create, name="create"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/", views.shoot_detail, name="detail"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/shots/", views.shot_add, name="shot_add"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/shots/from-posts/", views.shots_from_posts, name="shots_from_posts"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/shots/<uuid:shot_id>/", views.shot_update, name="shot_update"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/upload/", views.upload, name="upload"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/match/", views.match, name="match"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/done/", views.followups, name="followups"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/share/", views.share, name="share"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/send-prep/", views.send_prep, name="send_prep"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/call-sheet/", views.call_sheet, name="call_sheet"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/on-set/", views.on_set, name="on_set"),
    path("<uuid:workspace_id>/<uuid:shoot_id>/sync/", views.sync, name="sync"),
]
