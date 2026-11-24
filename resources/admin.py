from django.contrib import admin

from resources.models import Resource


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "owner", "timezone", "opens_at", "closes_at", "is_active"]
    list_filter = ["is_active", "timezone"]
    search_fields = ["name", "owner__username"]
    autocomplete_fields = ["owner"]
