from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import factory
from django.utils import timezone

from resources.models import Resource

KYIV = ZoneInfo("Europe/Kyiv")


def kyiv(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=KYIV)


def days_ahead(days: int) -> date:
    """A date `days` from today in Kyiv, so API tests always book in the future."""
    return timezone.now().astimezone(KYIV).date() + timedelta(days=days)


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    username = factory.Sequence(lambda number: f"user{number}")
    email = factory.LazyAttribute(lambda user: f"{user.username}@example.com")
    password = factory.django.Password("test-pass-123")


class ResourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Resource

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda number: f"Room {number}")
    timezone = "Europe/Kyiv"
    opens_at = time(8)
    closes_at = time(20)
    slot_minutes = 15
    min_minutes = 30
    max_minutes = 240
