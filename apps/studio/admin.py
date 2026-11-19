from django.contrib import admin

from apps.studio.models import StaffPermissionGrant


@admin.register(StaffPermissionGrant)
class StaffPermissionGrantAdmin(admin.ModelAdmin):
    list_display = ("org_membership", "key", "granted_by", "created_at")
    list_filter = ("key",)
    raw_id_fields = ("org_membership", "granted_by")

    def save_model(self, request, obj, form, change):
        if not obj.granted_by_id:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)
