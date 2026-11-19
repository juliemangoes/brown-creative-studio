from django.apps import AppConfig


class ClientOnboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.client_onboarding"
    verbose_name = "Client onboarding"

    def ready(self):
        from django.db.models.signals import post_migrate

        from apps.client_onboarding import portal  # noqa: F401  (registers the portal section and notice)

        post_migrate.connect(self._register_reminders, sender=self)

    @staticmethod
    def _register_reminders(sender, **kwargs):
        from apps.client_onboarding.tasks import send_onboarding_reminders
        from apps.common.background import register_recurring_task

        register_recurring_task(send_onboarding_reminders, repeat=3600, verbose_name="send_onboarding_reminders")
