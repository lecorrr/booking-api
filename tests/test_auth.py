import pytest
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

REGISTER = "/api/v1/auth/register/"


def registration(**fields) -> dict:
    return {
        "username": "olena",
        "email": "olena@example.com",
        "password": "a-long-pass-42",
    } | fields


def test_register_and_log_in(api):
    created = api.post(REGISTER, registration(), format="json")
    tokens = api.post(
        "/api/v1/auth/token/", {"username": "olena", "password": "a-long-pass-42"}, format="json"
    )

    assert created.status_code == 201
    assert User.objects.get(username="olena").check_password("a-long-pass-42")
    assert tokens.data["access"] and tokens.data["refresh"]


@pytest.mark.parametrize("password", ["short1", "12345678901", "password123", "olena2026"])
def test_weak_passwords_are_refused(api, password):
    response = api.post(REGISTER, registration(password=password), format="json")

    assert response.status_code == 400
    assert "password" in response.data


def test_an_email_in_another_case_is_taken(api):
    UserFactory(email="olena@example.com")

    response = api.post(REGISTER, registration(email="OLENA@example.com"), format="json")

    assert response.status_code == 400


def test_registration_is_throttled(api, monkeypatch):
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "register", "2/hour")

    codes = [
        api.post(
            REGISTER, registration(username=f"user{n}", email=f"user{n}@example.com")
        ).status_code
        for n in range(3)
    ]

    assert codes == [201, 201, 429]
