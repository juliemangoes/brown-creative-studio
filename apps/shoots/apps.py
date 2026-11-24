from django.apps import AppConfig


class ShootsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shoots"
    verbose_name = "Shoots"

    def ready(self):
        from apps.shoots import portal  # noqa: F401  (registers the portal section)
