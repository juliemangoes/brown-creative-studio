from django.apps import AppConfig


class BrandConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.brand"
    verbose_name = "Brand"

    def ready(self):
        from apps.brand import portal  # noqa: F401  (registers the Brand Hub portal section)
