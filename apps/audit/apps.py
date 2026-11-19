from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Studio-wide, append-only audit log of approvals, publishing, permissions and client changes."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"
    verbose_name = "Audit log"

    def ready(self):
        from apps.audit import signals  # noqa: F401
