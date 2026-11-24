import pytest

from bookings import services
from bookings.models import Booking, IdempotencyRecord
from tests.conftest import client_for
from tests.factories import UserFactory, kyiv

pytestmark = pytest.mark.django_db

BOOKINGS = "/api/v1/bookings/"


def body(resource, day, hour) -> dict:
    return {
        "resource": resource.pk,
        "start": kyiv(day, hour).isoformat(),
        "end": kyiv(day, hour + 1).isoformat(),
    }


def post(client, data, key):
    return client.post(BOOKINGS, data, format="json", HTTP_IDEMPOTENCY_KEY=key)


def test_a_repeated_request_gets_the_first_answer(user_api, resource, tomorrow):
    first = post(user_api, body(resource, tomorrow, 10), "phone-retry-0001")
    second = post(user_api, body(resource, tomorrow, 10), "phone-retry-0001")

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.data == first.data
    assert second["Idempotent-Replayed"] == "true"
    assert Booking.objects.count() == 1


def test_the_same_key_with_another_request_is_refused(user_api, resource, tomorrow):
    post(user_api, body(resource, tomorrow, 10), "phone-retry-0001")

    response = post(user_api, body(resource, tomorrow, 14), "phone-retry-0001")

    assert response.status_code == 422
    assert Booking.objects.count() == 1


def test_failures_are_not_stored_so_they_can_be_retried(user_api, resource, user, book, tomorrow):
    taken = book(resource, user, 10)

    conflict = post(user_api, body(resource, tomorrow, 10), "phone-retry-0002")
    services.cancel(taken.pk, user=resource.owner)
    retried = post(user_api, body(resource, tomorrow, 10), "phone-retry-0002")

    assert (conflict.status_code, retried.status_code) == (409, 201)


def test_keys_belong_to_one_user(user_api, resource, tomorrow):
    post(user_api, body(resource, tomorrow, 10), "shared-key-0001")
    other = post(client_for(UserFactory()), body(resource, tomorrow, 12), "shared-key-0001")

    assert other.status_code == 201
    assert IdempotencyRecord.objects.count() == 2


@pytest.mark.parametrize("key", ["short", "x" * 101, "has spaces in it", "ключ-ключ-ключ"])
def test_malformed_key(user_api, resource, tomorrow, key):
    assert post(user_api, body(resource, tomorrow, 10), key).status_code == 400


def test_without_a_key_nothing_is_stored(user_api, resource, tomorrow):
    user_api.post(BOOKINGS, body(resource, tomorrow, 10), format="json")

    assert not IdempotencyRecord.objects.exists()
