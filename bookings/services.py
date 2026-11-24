"""Every change to a booking goes through here, inside a transaction."""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q
from django.utils import timezone

from bookings.models import OVERLAP_CONSTRAINT, Booking
from bookings.periods import (
    Interval,
    Rules,
    check_period,
    day_window,
    free_intervals,
    next_slot_start,
)
from resources.models import Resource
from webhooks.services import queue_event

log = logging.getLogger(__name__)

REMINDER_BATCH = 100


class BookingError(Exception):
    """A rule stopped the change; the message is shown to the user."""


class SlotTaken(BookingError):
    pass


class WrongState(BookingError):
    pass


class TooLateToCancel(BookingError):
    pass


def rules_for(resource: Resource) -> Rules:
    return Rules(
        zone=resource.zone,
        opens_at=resource.opens_at,
        closes_at=resource.closes_at,
        slot_minutes=resource.slot_minutes,
        min_minutes=resource.min_minutes,
        max_minutes=resource.max_minutes,
        max_days_ahead=settings.BOOKING_MAX_DAYS_AHEAD,
    )


def hold(
    *,
    resource: Resource,
    customer,
    start: datetime,
    end: datetime,
    note: str = "",
    now: datetime | None = None,
) -> Booking:
    """Reserve the slot for a few minutes until the customer confirms."""
    now = now or timezone.now()
    check_period(rules_for(resource), start, end, now)
    try:
        with transaction.atomic():
            # A hold that ran out keeps blocking the slot until expire_holds runs; free it now.
            Booking.objects.filter(
                resource=resource, status=Booking.Status.HELD, held_until__lte=now
            ).update(status=Booking.Status.EXPIRED)
            return Booking.objects.create(
                resource=resource,
                customer=customer,
                period=DateTimeTZRange(start, end, "[)"),
                note=note,
                held_until=now + timedelta(minutes=settings.BOOKING_HOLD_MINUTES),
            )
    except IntegrityError as error:
        if violated_constraint(error) == OVERLAP_CONSTRAINT:
            raise SlotTaken("this time is already booked") from error
        raise


def violated_constraint(error: IntegrityError) -> str | None:
    diagnostics = getattr(error.__cause__, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


def confirm(booking_id: int, *, customer, now: datetime | None = None) -> Booking:
    now = now or timezone.now()
    with transaction.atomic():
        booking = locked_booking(Q(customer=customer), booking_id)
        if booking.status == Booking.Status.HELD and booking.held_until <= now:
            booking.status = Booking.Status.EXPIRED
            booking.save(update_fields=["status"])
        elif booking.status == Booking.Status.HELD:
            booking.status = Booking.Status.CONFIRMED
            booking.confirmed_at = now
            booking.save(update_fields=["status", "confirmed_at"])
            queue_event(booking.resource, "booking.confirmed", event_payload(booking))
    # Confirming twice is fine; anything else that is not confirmed now was too late.
    if booking.status != Booking.Status.CONFIRMED:
        raise WrongState(f"the booking is {booking.status} and cannot be confirmed")
    return booking


def cancel(booking_id: int, *, user, now: datetime | None = None) -> Booking:
    """The customer cancels with enough notice; the resource owner can cancel any time."""
    now = now or timezone.now()
    notice = timedelta(hours=settings.BOOKING_CANCEL_NOTICE_HOURS)
    with transaction.atomic():
        booking = locked_booking(Q(customer=user) | Q(resource__owner=user), booking_id)
        if booking.status not in Booking.ACTIVE:
            raise WrongState(f"the booking is already {booking.status}")
        if booking.end <= now:
            raise WrongState("the booking is over")
        by_owner = booking.resource.owner_id == user.id
        if (
            not by_owner
            and booking.status == Booking.Status.CONFIRMED
            and booking.start - now < notice
        ):
            raise TooLateToCancel(
                f"cancel at least {settings.BOOKING_CANCEL_NOTICE_HOURS} hours before the start"
            )
        was_confirmed = booking.status == Booking.Status.CONFIRMED
        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = now
        booking.save(update_fields=["status", "cancelled_at"])
        if was_confirmed:
            queue_event(booking.resource, "booking.cancelled", event_payload(booking))
    return booking


def locked_booking(visible_to: Q, booking_id: int) -> Booking:
    """Lock only the booking row, not the resource and user rows joined to it."""
    return (
        Booking.objects.select_for_update(of=("self",))
        .select_related("resource", "customer")
        .get(visible_to, pk=booking_id)
    )


def event_payload(booking: Booking) -> dict:
    return {
        "booking_id": booking.pk,
        "resource_id": booking.resource_id,
        "status": booking.status,
        "start": booking.start.isoformat(),
        "end": booking.end.isoformat(),
        "customer": booking.customer.username,
        "note": booking.note,
    }


def expire_holds(now: datetime | None = None) -> int:
    return Booking.objects.filter(
        status=Booking.Status.HELD, held_until__lte=now or timezone.now()
    ).update(status=Booking.Status.EXPIRED)


def send_due_reminders(*, send: Callable[[Booking], None], now: datetime | None = None) -> int:
    """Remind customers of confirmed bookings that start soon, once each.

    SKIP LOCKED lets two runs overlap safely: each takes rows the other is not handling.
    """
    now = now or timezone.now()
    horizon = now + timedelta(hours=settings.BOOKING_REMINDER_HOURS)
    sent = 0
    with transaction.atomic():
        due = (
            Booking.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("resource", "customer")
            .filter(
                status=Booking.Status.CONFIRMED,
                reminder_sent_at__isnull=True,
                period__startswith__gt=now,
                period__startswith__lte=horizon,
            )
            .order_by("id")[:REMINDER_BATCH]
        )
        for booking in due:
            try:
                send(booking)
            except OSError as error:  # SMTP and network errors; the next run tries again
                log.warning("reminder for booking=%s not sent: %s", booking.pk, error)
                continue
            booking.reminder_sent_at = now
            booking.save(update_fields=["reminder_sent_at"])
            sent += 1
    return sent


def availability(resource: Resource, day: date, now: datetime | None = None) -> list[Interval]:
    """Free stretches of the day that are long enough to book."""
    now = now or timezone.now()
    rules = rules_for(resource)
    opens, closes = day_window(rules, day)
    start = max(opens, next_slot_start(now, rules))
    if start >= closes:
        return []
    periods = (
        Booking.objects.filter(resource=resource, period__overlap=DateTimeTZRange(start, closes))
        .filter(
            Q(status=Booking.Status.CONFIRMED) | Q(status=Booking.Status.HELD, held_until__gt=now)
        )
        .values_list("period", flat=True)
    )
    busy = [(period.lower, period.upper) for period in periods]
    shortest = timedelta(minutes=rules.min_minutes)
    return [
        (free_start, free_end)
        for free_start, free_end in free_intervals((start, closes), busy)
        if free_end - free_start >= shortest
    ]
