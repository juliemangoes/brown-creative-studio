from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    verbose_name = "Results and reports"

    def ready(self):
        from apps.reports import portal  # noqa: F401  (registers the portal Results section)
