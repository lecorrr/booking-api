from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from bookings.models import Booking
from resources.models import Resource


class AwareDateTimeField(serializers.DateTimeField):
    """Refuses a time without a UTC offset instead of guessing which zone it was meant in."""

    default_error_messages = {"naive": "add a UTC offset, like 2026-12-01T10:00:00+02:00"}

    def to_internal_value(self, value):
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed is not None and parsed.tzinfo is None:
                self.fail("naive")
        return super().to_internal_value(value)


class BookingCreateSerializer(serializers.Serializer):
    resource = serializers.PrimaryKeyRelatedField(queryset=Resource.objects.filter(is_active=True))
    start = AwareDateTimeField()
    end = AwareDateTimeField()
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class BookingSerializer(serializers.ModelSerializer):
    resource_name = serializers.CharField(source="resource.name", read_only=True)
    customer = serializers.CharField(source="customer.username", read_only=True)
    start = serializers.DateTimeField(read_only=True)
    end = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id",
            "resource",
            "resource_name",
            "customer",
            "start",
            "end",
            "status",
            "note",
            "held_until",
            "confirmed_at",
            "cancelled_at",
            "created_at",
        ]
        read_only_fields = fields

    def to_representation(self, booking: Booking) -> dict:
        # Times read best on the clock of the place being booked.
        with timezone.override(booking.resource.zone):
            return super().to_representation(booking)


class BookingRangeQuerySerializer(serializers.Serializer):
    """Bookings that start in [start_from, start_to)."""

    start_from = AwareDateTimeField(required=False)
    start_to = AwareDateTimeField(required=False)


class AvailabilityQuerySerializer(serializers.Serializer):
    date = serializers.DateField()


class FreeIntervalSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()


class AvailabilitySerializer(serializers.Serializer):
    resource = serializers.IntegerField()
    date = serializers.DateField()
    timezone = serializers.CharField()
    free = FreeIntervalSerializer(many=True)
