from django.apps import AppConfig


class ClientsConfig(AppConfig):
    """Clients: each a Workspace with a profile, contacts, services and a retainer."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.clients"
    label = "clients"
    verbose_name = "Clients"
