from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from bookings import services
from bookings.models import Booking
from tests.factories import ResourceFactory, days_ahead, kyiv

pytestmark = pytest.mark.django_db


def free(api, resource, day) -> list[tuple[str, str]]:
    response = api.get(f"/api/v1/resources/{resource.pk}/availability/", {"date": day.isoformat()})
    return [(row["start"][11:16], row["end"][11:16]) for row in response.data["free"]]


def test_free_time_around_bookings(api, resource, user, book, tomorrow):
    book(resource, user, 10, minutes=90)
    book(resource, user, 14, confirm=False)  # a live hold blocks the slot too

    assert free(api, resource, tomorrow) == [
        ("08:00", "10:00"),
        ("11:30", "14:00"),
        ("15:00", "20:00"),
    ]


def test_expired_and_cancelled_bookings_do_not_block(api, resource, user, book, tomorrow):
    held = book(resource, user, 10, confirm=False)
    Booking.objects.filter(pk=held.pk).update(held_until=timezone.now() - timedelta(minutes=1))
    cancelled = book(resource, user, 12)
    services.cancel(cancelled.pk, user=resource.owner)

    assert free(api, resource, tomorrow) == [("08:00", "20:00")]


def test_gaps_shorter_than_a_booking_are_left_out(api, resource, user, book, tomorrow):
    book(resource, user, 8, minutes=60)
    services.hold(
        resource=resource, customer=user, start=kyiv(tomorrow, 9, 15), end=kyiv(tomorrow, 13, 15)
    )

    # 09:00-09:15 is free but shorter than the 30-minute minimum.
    assert free(api, resource, tomorrow) == [("13:15", "20:00")]


def test_times_are_shown_in_the_resource_zone(api, tomorrow):
    warsaw = ZoneInfo("Europe/Warsaw")
    resource = ResourceFactory(timezone="Europe/Warsaw")

    response = api.get(
        f"/api/v1/resources/{resource.pk}/availability/", {"date": tomorrow.isoformat()}
    )

    opening = datetime.combine(tomorrow, time(8), tzinfo=warsaw).isoformat()
    assert response.data["free"][0]["start"] == opening


def test_the_past_part_of_today_is_not_free(resource):
    day = date(2026, 12, 1)

    intervals = services.availability(resource, day, now=kyiv(day, 12, 7))

    assert intervals[0][0] == kyiv(day, 12, 15)


def test_a_past_day_has_nothing_free(api, resource):
    assert free(api, resource, days_ahead(-2)) == []


@pytest.mark.parametrize("params", [{}, {"date": "tomorrow"}, {"date": "2026-13-01"}])
def test_invalid_date(api, resource, params):
    response = api.get(f"/api/v1/resources/{resource.pk}/availability/", params)

    assert response.status_code == 400
