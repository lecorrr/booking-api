from django.contrib import admin

from webhooks.models import WebhookDelivery


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["id", "resource", "event", "status", "attempts", "created_at"]
    list_filter = ["status", "event"]
    readonly_fields = [
        "resource",
        "url",
        "event",
        "payload",
        "status",
        "attempts",
        "last_error",
        "delivered_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False
