from django.urls import path

from . import views

app_name = "brand"

urlpatterns = [
    path("<uuid:workspace_id>/", views.brand_profile, name="profile"),
    path("<uuid:workspace_id>/profile/", views.brand_profile_save, name="profile_save"),
    path("<uuid:workspace_id>/publish/", views.brand_publish, name="publish"),
    path("<uuid:workspace_id>/stages/", views.brand_stages, name="stages"),
    path("<uuid:workspace_id>/stages/start/", views.brand_stages_start, name="stages_start"),
    path("<uuid:workspace_id>/stages/<uuid:stage_id>/", views.brand_stage_save, name="stage_save"),
    path("<uuid:workspace_id>/stages/<uuid:stage_id>/send/", views.brand_stage_send, name="stage_send"),
    path("<uuid:workspace_id>/files/", views.brand_files, name="files"),
    path("<uuid:workspace_id>/files/upload/", views.brand_files_upload, name="files_upload"),
    path("<uuid:workspace_id>/files/<uuid:asset_id>/", views.brand_file_download, name="file"),
    path("<uuid:workspace_id>/files/<uuid:asset_id>/visibility/", views.brand_file_visibility, name="file_visibility"),
    path("<uuid:workspace_id>/files/<uuid:asset_id>/delete/", views.brand_file_delete, name="file_delete"),
]
