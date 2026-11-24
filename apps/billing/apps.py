from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    verbose_name = "Billing"

    def ready(self):
        from django.db.models.signals import post_migrate

        from apps.billing import portal  # noqa: F401  (registers the portal section and notices)

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.billing.tasks import billing_daily
        from apps.common.background import register_recurring_task

        register_recurring_task(billing_daily, repeat=3600, verbose_name="billing_daily")
