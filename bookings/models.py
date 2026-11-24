from datetime import datetime

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import Q

from resources.models import Resource

OVERLAP_CONSTRAINT = "booking_no_overlap"


class Booking(models.Model):
    class Status(models.TextChoices):
        HELD = "held"
        CONFIRMED = "confirmed"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    # Statuses that occupy the time slot.
    ACTIVE = (Status.HELD, Status.CONFIRMED)

    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="bookings")
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings"
    )
    period = DateTimeRangeField()  # [start, end)
    status = models.CharField(max_length=20, choices=Status, default=Status.HELD)
    note = models.CharField(max_length=200, blank=True)
    held_until = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            # Two active bookings of one resource can never overlap, however many requests
            # race for the same slot. Needs the btree_gist extension for the "=" part.
            ExclusionConstraint(
                name=OVERLAP_CONSTRAINT,
                expressions=[
                    ("resource", RangeOperators.EQUAL),
                    ("period", RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=["held", "confirmed"]),
            ),
            models.CheckConstraint(
                condition=Q(
                    period__isempty=False, period__lower_inf=False, period__upper_inf=False
                ),
                name="booking_period_bounded",
            ),
            models.CheckConstraint(
                condition=~Q(status="held") | Q(held_until__isnull=False),
                name="booking_hold_has_deadline",
            ),
        ]
        indexes = [models.Index(fields=["status", "held_until"], name="booking_status_hold_idx")]

    def __str__(self) -> str:
        return f"{self.resource} {self.period.lower:%Y-%m-%d %H:%M} ({self.status})"

    @property
    def start(self) -> datetime:
        return self.period.lower

    @property
    def end(self) -> datetime:
        return self.period.upper


class IdempotencyRecord(models.Model):
    """The response to a request sent with an Idempotency-Key, replayed if it comes again."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="idempotency_key_per_user")
        ]

    def __str__(self) -> str:
        return f"{self.key} ({self.response_status})"
