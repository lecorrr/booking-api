from datetime import time
from urllib.parse import urlsplit

from rest_framework import serializers

from resources.models import Resource

RULE_FIELDS = ("opens_at", "closes_at", "slot_minutes", "min_minutes", "max_minutes")


def minutes_of(moment: time) -> int:
    return moment.hour * 60 + moment.minute


class ResourceSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = Resource
        fields = [
            "id",
            "name",
            "description",
            "timezone",
            "opens_at",
            "closes_at",
            "slot_minutes",
            "min_minutes",
            "max_minutes",
            "webhook_url",
            "is_active",
            "owner",
        ]

    def validate_webhook_url(self, url: str) -> str:
        if url and urlsplit(url).scheme != "https":
            raise serializers.ValidationError("webhooks are sent to https:// URLs only")
        return url

    def validate(self, attrs: dict) -> dict:
        # Check the resource as it will be saved: on PATCH the other fields come from the
        # instance, on create from the model defaults.
        current = self.instance or Resource()
        value = {name: attrs.get(name, getattr(current, name)) for name in RULE_FIELDS}
        if value["closes_at"] <= value["opens_at"]:
            raise serializers.ValidationError({"closes_at": ["must be after opens_at"]})
        if value["min_minutes"] > value["max_minutes"]:
            raise serializers.ValidationError({"min_minutes": ["must not exceed max_minutes"]})
        for name in ("min_minutes", "max_minutes"):
            if value[name] % value["slot_minutes"]:
                raise serializers.ValidationError(
                    {name: [f"must be a multiple of slot_minutes ({value['slot_minutes']})"]}
                )
        open_minutes = minutes_of(value["closes_at"]) - minutes_of(value["opens_at"])
        if value["min_minutes"] > open_minutes:
            raise serializers.ValidationError({"min_minutes": ["longer than the opening hours"]})
        return attrs

    def to_representation(self, resource: Resource) -> dict:
        data = super().to_representation(resource)
        request = self.context.get("request")
        if request is None or request.user.id != resource.owner_id:
            data.pop("webhook_url")  # owner's private configuration
        return data


class WebhookSecretSerializer(serializers.Serializer):
    secret = serializers.CharField()
