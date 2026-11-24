from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from bookings.periods import (
    PeriodError,
    Rules,
    check_period,
    day_window,
    free_intervals,
    next_slot_start,
)

KYIV = ZoneInfo("Europe/Kyiv")
DAY = date(2026, 12, 1)
NOW = datetime(2026, 11, 30, 12, 0, tzinfo=KYIV)
RULES = Rules(
    zone=KYIV,
    opens_at=time(8),
    closes_at=time(20),
    slot_minutes=15,
    min_minutes=30,
    max_minutes=240,
    max_days_ahead=90,
)


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=KYIV)


def test_valid_period():
    check_period(RULES, at(10), at(11, 30), NOW)


def test_same_period_given_in_utc_is_valid():
    utc = ZoneInfo("UTC")
    check_period(RULES, at(10).astimezone(utc), at(11).astimezone(utc), NOW)


@pytest.mark.parametrize(
    "start, end, message",
    [
        (datetime(2026, 12, 1, 10), at(11), "UTC offset"),
        (at(11), at(10), "after start"),
        (at(10), at(10), "after start"),
        (at(10, day=date(2026, 11, 30)), at(11, day=date(2026, 11, 30)), "future"),
        (at(10, day=date(2027, 3, 31)), at(11, day=date(2027, 3, 31)), "days ahead"),
        (at(10), at(10, 15), "lasts"),
        (at(9), at(13, 15), "lasts"),
        (at(7, 30), at(9), "open from"),
        (at(19), at(20, 30), "open from"),
        (at(10, 5), at(11, 5), "boundaries"),
        (at(10).replace(second=30), at(11), "boundaries"),
    ],
)
def test_invalid_period(start, end, message):
    with pytest.raises(PeriodError, match=message):
        check_period(RULES, start, end, NOW)


def test_a_booking_cannot_cross_midnight():
    late = Rules(KYIV, time(0), time(23, 45), 15, 30, 240, 90)

    with pytest.raises(PeriodError, match="same day"):
        check_period(late, at(23), at(0, 30, day=DAY + timedelta(days=1)), NOW)


def test_duration_is_real_time_across_a_clock_change():
    # Clocks in Kyiv jump from 03:00 to 04:00 on 28 March 2027: 02:00 to 05:00 is two hours.
    night = Rules(KYIV, time(0), time(6), 15, 30, 120, 200)
    change_day = date(2027, 3, 28)

    check_period(night, at(2, day=change_day), at(5, day=change_day), NOW)


def test_day_window_follows_the_resource_zone():
    opens, closes = day_window(RULES, DAY)

    assert (opens.isoformat(), closes.isoformat()) == (
        "2026-12-01T06:00:00+00:00",
        "2026-12-01T18:00:00+00:00",
    )


def test_day_window_on_the_day_the_clocks_go_forward():
    opens, closes = day_window(RULES, date(2027, 3, 28))

    assert closes - opens == timedelta(hours=12)  # 08:00 and 20:00 are both summer time
    assert opens.isoformat() == "2027-03-28T05:00:00+00:00"


@pytest.mark.parametrize(
    "moment, expected",
    [
        (at(10), at(10)),
        (at(10, 1), at(10, 15)),
        (at(10).replace(second=1), at(10, 15)),
        (at(10, 50), at(11)),
        (at(23, 50), at(0, day=DAY + timedelta(days=1))),
    ],
)
def test_next_slot_start(moment, expected):
    assert next_slot_start(moment, RULES) == expected


WINDOW = (at(8), at(20))


@pytest.mark.parametrize(
    "busy, free",
    [
        ([], [(at(8), at(20))]),
        ([(at(10), at(11))], [(at(8), at(10)), (at(11), at(20))]),
        ([(at(10), at(12)), (at(11), at(13))], [(at(8), at(10)), (at(13), at(20))]),
        ([(at(10), at(11)), (at(11), at(12))], [(at(8), at(10)), (at(12), at(20))]),
        ([(at(7), at(9)), (at(19), at(21))], [(at(9), at(19))]),
        ([(at(7), at(21))], []),
        ([(at(13), at(14)), (at(9), at(10))], [(at(8), at(9)), (at(10), at(13)), (at(14), at(20))]),
    ],
    ids=["empty", "one", "overlapping", "adjacent", "sticking out", "all day", "unsorted"],
)
def test_free_intervals(busy, free):
    assert free_intervals(WINDOW, busy) == free
