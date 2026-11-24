from django.apps import AppConfig


class PlanningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.planning"
    verbose_name = "Planning"

    def ready(self):
        from apps.planning import portal, signals  # noqa: F401  (portal calendar; stage follows submission)
