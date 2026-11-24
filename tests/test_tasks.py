from datetime import timedelta

import pytest
from django.core import mail
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone

from bookings import services, tasks
from bookings.models import Booking, IdempotencyRecord

pytestmark = pytest.mark.django_db


def booking_in(resource, customer, hours: float, status="confirmed", **fields) -> Booking:
    start = timezone.now() + timedelta(hours=hours)
    return Booking.objects.create(
        resource=resource,
        customer=customer,
        period=DateTimeTZRange(start, start + timedelta(hours=1)),
        status=status,
        **fields,
    )


def test_holds_that_ran_out_expire(resource, user):
    now = timezone.now()
    stale = booking_in(resource, user, 30, status="held", held_until=now - timedelta(seconds=1))
    live = booking_in(resource, user, 40, status="held", held_until=now + timedelta(minutes=5))

    assert tasks.expire_holds.delay().get() == 1

    stale.refresh_from_db()
    live.refresh_from_db()
    assert (stale.status, live.status) == ("expired", "held")


def test_a_reminder_goes_out_once_for_bookings_starting_soon(resource, user):
    soon = booking_in(resource, user, 3)
    booking_in(resource, user, 48)  # too far ahead
    booking_in(resource, user, 5, status="cancelled")
    booking_in(resource, user, 6, status="held", held_until=timezone.now() + timedelta(minutes=5))

    first_run = tasks.send_reminders.delay().get()
    second_run = tasks.send_reminders.delay().get()

    assert (first_run, second_run) == (1, 0)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert resource.name in mail.outbox[0].subject
    soon.refresh_from_db()
    assert soon.reminder_sent_at is not None


def test_a_reminder_that_fails_is_tried_again_next_time(resource, user):
    booking = booking_in(resource, user, 3)

    def smtp_down(booking):
        raise ConnectionRefusedError("smtp is down")

    assert services.send_due_reminders(send=smtp_down) == 0
    booking.refresh_from_db()
    assert booking.reminder_sent_at is None
    assert tasks.send_reminders.delay().get() == 1


def test_reminder_shows_the_local_time_of_the_resource(resource, user):
    booking = booking_in(resource, user, 3)

    tasks.send_reminders.delay()

    local_start = booking.start.astimezone(resource.zone)
    assert f"{local_start:%H:%M}" in mail.outbox[0].subject


def test_old_idempotency_records_are_purged(user):
    old = IdempotencyRecord.objects.create(
        user=user, key="old-key-0001", request_hash="x", response_status=201, response_body={}
    )
    IdempotencyRecord.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    IdempotencyRecord.objects.create(
        user=user, key="new-key-0001", request_hash="x", response_status=201, response_body={}
    )

    assert tasks.purge_idempotency_records.delay().get() == 1
    assert list(IdempotencyRecord.objects.values_list("key", flat=True)) == ["new-key-0001"]
