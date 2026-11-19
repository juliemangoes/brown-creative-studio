from django.urls import path

from . import views

app_name = "clients"

urlpatterns = [
    path("", views.client_list, name="list"),
    path("new/", views.client_create, name="create"),
    path("<uuid:workspace_id>/", views.client_detail, name="detail"),
    path("<uuid:workspace_id>/profile/", views.profile_update, name="profile_update"),
    path("<uuid:workspace_id>/contacts/", views.client_contacts, name="contacts"),
    path("<uuid:workspace_id>/contacts/add/", views.contact_save, name="contact_add"),
    path("<uuid:workspace_id>/contacts/<uuid:contact_id>/", views.contact_save, name="contact_edit"),
    path("<uuid:workspace_id>/contacts/<uuid:contact_id>/delete/", views.contact_delete, name="contact_delete"),
    path("<uuid:workspace_id>/services/", views.client_services, name="services"),
    path("<uuid:workspace_id>/services/save/", views.service_save, name="service_save"),
    path("<uuid:workspace_id>/retainer/", views.retainer_save, name="retainer_save"),
    path("<uuid:workspace_id>/activity/", views.client_activity, name="activity"),
]
