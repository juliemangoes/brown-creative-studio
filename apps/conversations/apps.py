from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.conversations"
    verbose_name = "Messages"

    def ready(self):
        from apps.conversations import portal  # noqa: F401  (registers the portal section)
