from django.apps import AppConfig


class StudioConfig(AppConfig):
    """The studio shell (navigation, client switcher, command menu) and, from Phase 2, Today."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.studio"
    label = "studio"
    verbose_name = "Studio"
