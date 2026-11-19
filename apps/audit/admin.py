from django.contrib import admin

from apps.audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: the audit log is append-only, here as everywhere."""

    list_display = ("created_at", "organization", "workspace", "actor_label", "verb", "summary")
    list_filter = ("verb",)
    search_fields = ("summary", "actor_label", "verb")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
