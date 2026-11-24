import secrets
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

SLOT_CHOICES = [5, 10, 15, 20, 30, 60]


def validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValidationError(f"{name!r} is not a known time zone") from error


def new_webhook_secret() -> str:
    return secrets.token_hex(32)


class Resource(models.Model):
    """Something that is booked by the hour: a meeting room, a court, a barber's chair."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="resources"
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    timezone = models.CharField(
        max_length=64, default="Europe/Kyiv", validators=[validate_timezone]
    )
    # Bookable hours, the same every day, in the resource's own time zone.
    opens_at = models.TimeField(default=time(9))
    closes_at = models.TimeField(default=time(18))
    slot_minutes = models.PositiveSmallIntegerField(
        default=15, choices=[(minutes, f"{minutes} min") for minutes in SLOT_CHOICES]
    )
    min_minutes = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(5), MaxValueValidator(24 * 60)]
    )
    max_minutes = models.PositiveSmallIntegerField(
        default=240, validators=[MinValueValidator(5), MaxValueValidator(24 * 60)]
    )
    webhook_url = models.URLField(max_length=500, blank=True)
    webhook_secret = models.CharField(max_length=64, default=new_webhook_secret, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(closes_at__gt=F("opens_at")), name="resource_opens_before_closing"
            ),
            models.CheckConstraint(
                condition=Q(min_minutes__lte=F("max_minutes")), name="resource_min_below_max"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
