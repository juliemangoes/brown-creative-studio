from django.urls import path

from . import views

app_name = "planning"

urlpatterns = [
    path("", views.all_clients, name="all"),
    path("<uuid:workspace_id>/", views.board, name="board"),
    path("<uuid:workspace_id>/add/", views.quick_add, name="quick_add"),
    path("<uuid:workspace_id>/ideas/<uuid:idea_id>/plan/", views.idea_to_plan, name="plan_idea"),
    path("<uuid:workspace_id>/posts/<uuid:post_id>/", views.post_detail, name="post"),
    path("<uuid:workspace_id>/posts/<uuid:post_id>/stage/", views.stage, name="stage"),
    path("<uuid:workspace_id>/campaigns/", views.campaigns, name="campaigns"),
    path("<uuid:workspace_id>/campaigns/<uuid:campaign_id>/", views.campaigns, name="campaign"),
    path("<uuid:workspace_id>/key-dates/", views.key_dates, name="key_dates"),
]
