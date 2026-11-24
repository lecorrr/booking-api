from datetime import timedelta

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from bookings import services
from tests.factories import ResourceFactory, UserFactory, days_ahead, kyiv


@pytest.fixture(autouse=True)
def clear_throttle_counters():
    cache.clear()


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def user_api(user) -> APIClient:
    return client_for(user)


@pytest.fixture
def resource(db):
    return ResourceFactory()


@pytest.fixture
def owner_api(resource) -> APIClient:
    return client_for(resource.owner)


@pytest.fixture
def tomorrow():
    return days_ahead(1)


@pytest.fixture
def book(tomorrow):
    """Hold, and by default confirm, a booking tomorrow from hour to hour + length."""

    def make(resource, customer, hour, *, minutes=60, confirm=True):
        start = kyiv(tomorrow, hour)
        booking = services.hold(
            resource=resource,
            customer=customer,
            start=start,
            end=start + timedelta(minutes=minutes),
        )
        return services.confirm(booking.pk, customer=customer) if confirm else booking

    return make


def client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client
