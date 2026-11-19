from django.contrib import admin

from apps.clients.models import ClientProfile, Contact, Retainer, ServiceLine


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("workspace", "stage", "industry", "currency", "started_on")
    list_filter = ("stage",)
    search_fields = ("workspace__name", "legal_name")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "email", "is_primary", "is_approver", "is_billing")
    search_fields = ("name", "email", "workspace__name")
    raw_id_fields = ("user",)


admin.site.register(ServiceLine)
admin.site.register(Retainer)
