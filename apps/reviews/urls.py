from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("<uuid:workspace_id>/posts/<uuid:post_id>/", views.post_review, name="post"),
    path("<uuid:workspace_id>/posts/<uuid:post_id>/override/", views.override, name="override"),
    path("<uuid:workspace_id>/portal-links/", views.portal_links, name="portal_links"),
    path("<uuid:workspace_id>/portal-links/<uuid:token_id>/revoke/", views.revoke_link, name="revoke_link"),
    path("<uuid:workspace_id>/portal-links/person/<uuid:membership_id>/revoke/", views.revoke_all, name="revoke_all"),
    path("<uuid:workspace_id>/rule/", views.approval_rule, name="rule"),
]
