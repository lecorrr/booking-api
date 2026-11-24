"""Booking rules that need no database: is a period bookable, and what is still free.

Datetimes are timezone-aware throughout. Opening hours and slot boundaries are wall-clock
times in the resource's zone; durations and comparisons use UTC. (Python ignores the offsets
when both datetimes share one tzinfo, which gives wall-clock answers on the two days a
year the clocks change.)
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class Rules:
    zone: ZoneInfo
    opens_at: time
    closes_at: time
    slot_minutes: int
    min_minutes: int
    max_minutes: int
    max_days_ahead: int


class PeriodError(ValueError):
    """The period breaks a rule; the message is shown to the user."""


def check_period(rules: Rules, start: datetime, end: datetime, now: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise PeriodError("start and end need a UTC offset, like 2026-12-01T10:00:00+02:00")
    start_utc, end_utc, now_utc = (moment.astimezone(UTC) for moment in (start, end, now))
    if end_utc <= start_utc:
        raise PeriodError("end must be after start")
    if start_utc <= now_utc:
        raise PeriodError("start must be in the future")
    if start_utc - now_utc > timedelta(days=rules.max_days_ahead):
        raise PeriodError(f"bookings open {rules.max_days_ahead} days ahead")
    minutes = (end_utc - start_utc) / timedelta(minutes=1)
    if not rules.min_minutes <= minutes <= rules.max_minutes:
        raise PeriodError(f"a booking lasts {rules.min_minutes} to {rules.max_minutes} minutes")
    local_start, local_end = start.astimezone(rules.zone), end.astimezone(rules.zone)
    if local_start.date() != local_end.date():
        raise PeriodError("a booking starts and ends on the same day")
    if local_start.time() < rules.opens_at or local_end.time() > rules.closes_at:
        raise PeriodError(
            f"open from {rules.opens_at:%H:%M} to {rules.closes_at:%H:%M} ({rules.zone.key})"
        )
    if not (on_slot_boundary(local_start, rules) and on_slot_boundary(local_end, rules)):
        raise PeriodError(f"start and end must be on {rules.slot_minutes}-minute boundaries")


def on_slot_boundary(local: datetime, rules: Rules) -> bool:
    minutes = local.hour * 60 + local.minute
    return local.second == 0 and local.microsecond == 0 and minutes % rules.slot_minutes == 0


def day_window(rules: Rules, day: date) -> Interval:
    """The day's opening hours as UTC instants."""
    return (
        datetime.combine(day, rules.opens_at, tzinfo=rules.zone).astimezone(UTC),
        datetime.combine(day, rules.closes_at, tzinfo=rules.zone).astimezone(UTC),
    )


def next_slot_start(moment: datetime, rules: Rules) -> datetime:
    """The first slot boundary at or after moment, in the resource's zone."""
    local = moment.astimezone(rules.zone)
    minutes = local.hour * 60 + local.minute + (1 if local.second or local.microsecond else 0)
    rounded = -(-minutes // rules.slot_minutes) * rules.slot_minutes
    midnight = datetime.combine(local.date(), time(0), tzinfo=rules.zone)
    # Wall-clock arithmetic on purpose: slots are 10:00, 10:15... on the clock in the room.
    return (midnight + timedelta(minutes=rounded)).astimezone(UTC)


def free_intervals(window: Interval, busy: list[Interval]) -> list[Interval]:
    """The parts of window that no busy interval covers, in UTC. Busy intervals may overlap."""
    cursor, window_end = (moment.astimezone(UTC) for moment in window)
    free = []
    in_utc = sorted((start.astimezone(UTC), end.astimezone(UTC)) for start, end in busy)
    for busy_start, busy_end in in_utc:
        if busy_end <= cursor:
            continue
        if busy_start >= window_end:
            break
        if busy_start > cursor:
            free.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < window_end:
        free.append((cursor, window_end))
    return free
