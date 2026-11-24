from django.apps import AppConfig


class PublishingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.publishing"
    verbose_name = "Publishing"

    def ready(self):
        from django.db.models.signals import post_migrate

        from apps.publishing import signals  # noqa: F401  (retry backstop)

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task
        from apps.publishing.tasks import publishing_checks

        # General lane (apps/common/worker.py): never takes publishing capacity.
        register_recurring_task(publishing_checks, repeat=900, verbose_name="publishing_checks")
