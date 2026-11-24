from django.contrib import admin

from bookings.models import Booking, IdempotencyRecord


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["id", "resource", "customer", "period", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["resource__name", "customer__username"]
    autocomplete_fields = ["resource", "customer"]


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "key", "response_status", "created_at"]
    readonly_fields = ["user", "key", "request_hash", "response_status", "response_body"]
