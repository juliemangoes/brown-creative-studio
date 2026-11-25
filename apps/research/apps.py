from django.apps import AppConfig


class ResearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.research"
    verbose_name = "Research"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task
        from apps.research.tasks import research_weekly

        # General lane (apps/common/worker.py): never takes publishing capacity.
        register_recurring_task(research_weekly, repeat=3600, verbose_name="research_weekly")
