from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone

from bookings.models import Booking
from tests.factories import ResourceFactory, UserFactory, days_ahead, kyiv

pytestmark = pytest.mark.django_db

BOOKINGS = "/api/v1/bookings/"


def slot(resource, day, hour, minute=0, minutes=60, **extra) -> dict:
    start = kyiv(day, hour, minute)
    return {
        "resource": resource.pk,
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=minutes)).isoformat(),
    } | extra


def test_hold_reserves_the_slot_for_a_few_minutes(user_api, resource, tomorrow):
    response = user_api.post(BOOKINGS, slot(resource, tomorrow, 10, note="standup"), format="json")

    assert response.status_code == 201
    assert response.data["status"] == "held"
    booking = Booking.objects.get(pk=response.data["id"])
    assert timedelta(minutes=9) < booking.held_until - timezone.now() <= timedelta(minutes=10)


def test_an_overlapping_booking_is_a_conflict(user_api, resource, tomorrow):
    user_api.post(BOOKINGS, slot(resource, tomorrow, 10), format="json")

    response = user_api.post(BOOKINGS, slot(resource, tomorrow, 10, 30), format="json")

    assert response.status_code == 409


def test_back_to_back_bookings_are_fine(user_api, resource, tomorrow):
    codes = [
        user_api.post(BOOKINGS, slot(resource, tomorrow, hour), format="json").status_code
        for hour in (10, 11)
    ]

    assert codes == [201, 201]  # [10:00, 11:00) and [11:00, 12:00) do not overlap


def test_the_same_time_on_another_resource_is_fine(user_api, resource, tomorrow):
    other = ResourceFactory()
    user_api.post(BOOKINGS, slot(resource, tomorrow, 10), format="json")

    assert user_api.post(BOOKINGS, slot(other, tomorrow, 10), format="json").status_code == 201


@pytest.mark.parametrize(
    "change, field",
    [
        ({"start": "2030-01-01T10:00:00"}, "start"),  # no UTC offset
        ({"resource": 999_999}, "resource"),
        ({"note": "x" * 201}, "note"),
    ],
)
def test_invalid_request(user_api, resource, tomorrow, change, field):
    response = user_api.post(BOOKINGS, slot(resource, tomorrow, 10) | change, format="json")

    assert response.status_code == 400
    assert field in response.data


def test_rules_of_the_resource_are_applied(user_api, resource, tomorrow):
    response = user_api.post(BOOKINGS, slot(resource, tomorrow, 19, 30), format="json")

    assert response.status_code == 400
    assert "open from 08:00 to 20:00" in response.data["start"][0]


def test_a_retired_resource_cannot_be_booked(user_api, tomorrow):
    retired = ResourceFactory(is_active=False)

    assert user_api.post(BOOKINGS, slot(retired, tomorrow, 10), format="json").status_code == 400


def test_booking_needs_a_login(api, resource, tomorrow):
    assert api.post(BOOKINGS, slot(resource, tomorrow, 10), format="json").status_code == 401


def test_confirm(user_api, resource, user, book):
    held = book(resource, user, 10, confirm=False)

    first = user_api.post(f"{BOOKINGS}{held.pk}/confirm/")
    again = user_api.post(f"{BOOKINGS}{held.pk}/confirm/")

    assert (first.status_code, again.status_code) == (200, 200)
    assert first.data["status"] == "confirmed"


def test_a_hold_that_ran_out_cannot_be_confirmed(user_api, resource, user, book):
    held = book(resource, user, 10, confirm=False)
    Booking.objects.filter(pk=held.pk).update(held_until=timezone.now() - timedelta(seconds=1))

    response = user_api.post(f"{BOOKINGS}{held.pk}/confirm/")

    assert response.status_code == 409
    held.refresh_from_db()
    assert held.status == "expired"


def test_an_expired_hold_frees_the_slot_for_someone_else(api, resource, user, book, tomorrow):
    held = book(resource, user, 10, confirm=False)
    Booking.objects.filter(pk=held.pk).update(held_until=timezone.now() - timedelta(seconds=1))
    other = UserFactory()
    api.force_authenticate(other)

    assert api.post(BOOKINGS, slot(resource, tomorrow, 10), format="json").status_code == 201


def test_only_the_customer_confirms(owner_api, resource, user, book):
    held = book(resource, user, 10, confirm=False)

    assert owner_api.post(f"{BOOKINGS}{held.pk}/confirm/").status_code == 404


def test_customer_cancels_with_notice_and_frees_the_slot(user_api, resource, user, book, tomorrow):
    booking = book(resource, user, 10)

    response = user_api.post(f"{BOOKINGS}{booking.pk}/cancel/")

    assert response.data["status"] == "cancelled"
    assert user_api.post(BOOKINGS, slot(resource, tomorrow, 10), format="json").status_code == 201


def test_customer_cannot_cancel_too_late_but_the_owner_can(user_api, owner_api, resource, user):
    soon = timezone.now() + timedelta(hours=1)
    booking = Booking.objects.create(
        resource=resource,
        customer=user,
        period=DateTimeTZRange(soon, soon + timedelta(hours=1)),
        status="confirmed",
    )

    assert user_api.post(f"{BOOKINGS}{booking.pk}/cancel/").status_code == 409
    assert owner_api.post(f"{BOOKINGS}{booking.pk}/cancel/").status_code == 200


def test_cancelling_twice_is_a_conflict(user_api, resource, user, book):
    booking = book(resource, user, 10)
    user_api.post(f"{BOOKINGS}{booking.pk}/cancel/")

    assert user_api.post(f"{BOOKINGS}{booking.pk}/cancel/").status_code == 409


def test_strangers_see_nothing(api, resource, user, book):
    booking = book(resource, user, 10)
    api.force_authenticate(UserFactory())

    assert api.get(f"{BOOKINGS}{booking.pk}/").status_code == 404
    assert api.post(f"{BOOKINGS}{booking.pk}/cancel/").status_code == 404
    assert api.get(BOOKINGS).data["count"] == 0


def test_list_shows_my_bookings_and_the_owner_can_open_one(
    user_api, owner_api, resource, user, book
):
    booking = book(resource, user, 10)

    assert [row["id"] for row in user_api.get(BOOKINGS).data["results"]] == [booking.pk]
    assert owner_api.get(f"{BOOKINGS}{booking.pk}/").data["customer"] == user.username


def test_booking_times_are_shown_in_the_resource_zone(user_api):
    warsaw = ResourceFactory(timezone="Europe/Warsaw")
    day = days_ahead(2)
    start = f"{day.isoformat()}T10:00:00+01:00"

    response = user_api.post(
        BOOKINGS,
        {"resource": warsaw.pk, "start": start, "end": f"{day.isoformat()}T11:00:00+01:00"},
        format="json",
    )

    assert response.data["start"] == start


def test_database_refuses_overlaps_even_without_the_api(resource, user, tomorrow):
    period = DateTimeTZRange(kyiv(tomorrow, 10), kyiv(tomorrow, 11))
    Booking.objects.create(resource=resource, customer=user, period=period, status="confirmed")

    with pytest.raises(IntegrityError, match="booking_no_overlap"):
        Booking.objects.create(
            resource=resource,
            customer=UserFactory(),
            period=DateTimeTZRange(kyiv(tomorrow, 10, 30), kyiv(tomorrow, 12)),
            status="confirmed",
        )


@pytest.mark.parametrize("bounds", [(None, "end"), ("start", None)])
def test_database_refuses_open_ended_periods(resource, user, tomorrow, bounds):
    start = kyiv(tomorrow, 10) if bounds[0] else None
    end = kyiv(tomorrow, 11) if bounds[1] else None

    with pytest.raises(IntegrityError, match="booking_period_bounded"):
        Booking.objects.create(
            resource=resource,
            customer=user,
            period=DateTimeTZRange(start, end),
            status="confirmed",
        )
