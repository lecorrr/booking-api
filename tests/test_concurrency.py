"""Real concurrent transactions, each thread with its own database connection.

These tests commit (transaction=True), because a row inserted by one thread must be
visible to, and block, the others.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections

from bookings import services
from bookings.models import Booking
from tests.conftest import client_for
from tests.factories import ResourceFactory, UserFactory, kyiv

pytestmark = pytest.mark.django_db(transaction=True)


def race(attempts: list, *args) -> list:
    """Run every attempt at the same moment; return what each one returned."""
    barrier = threading.Barrier(len(attempts))

    def run(attempt):
        try:
            barrier.wait()
            return attempt(*args)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(len(attempts)) as pool:
        return list(pool.map(run, attempts))


def test_many_customers_racing_for_one_slot_get_exactly_one_booking(tomorrow):
    resource = ResourceFactory()
    customers = [UserFactory() for _ in range(8)]

    def attempt_for(customer):
        def attempt():
            try:
                services.hold(
                    resource=resource,
                    customer=customer,
                    start=kyiv(tomorrow, 10),
                    end=kyiv(tomorrow, 11),
                )
                return "booked"
            except services.SlotTaken:
                return "taken"

        return attempt

    results = race([attempt_for(customer) for customer in customers])

    assert sorted(results) == ["booked"] + ["taken"] * 7
    assert Booking.objects.filter(status__in=Booking.ACTIVE).count() == 1


def test_a_request_sent_twice_at_once_books_once(tomorrow):
    resource = ResourceFactory()
    customer = UserFactory()
    body = {
        "resource": resource.pk,
        "start": kyiv(tomorrow, 10).isoformat(),
        "end": kyiv(tomorrow, 11).isoformat(),
    }

    def attempt():
        response = client_for(customer).post(
            "/api/v1/bookings/", body, format="json", HTTP_IDEMPOTENCY_KEY="double-tap-0001"
        )
        return response.status_code, response.data["id"]

    results = race([attempt, attempt])

    assert [code for code, _ in results] == [201, 201]
    assert results[0][1] == results[1][1]
    assert Booking.objects.count() == 1
