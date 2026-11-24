import pytest
from django.core.management import CommandError, call_command

from bookings.models import Booking
from core import checks
from resources.models import Resource

pytestmark = pytest.mark.django_db


def test_healthy(api):
    response = api.get("/api/v1/health/")

    assert response.status_code == 200
    assert response.data["checks"] == {"postgres": "ok", "redis": "ok"}


def test_a_broken_dependency_makes_it_503(api, monkeypatch):
    def redis_down():
        raise ConnectionError("refused")

    monkeypatch.setitem(checks.CHECKS, "redis", redis_down)

    response = api.get("/api/v1/health/")

    assert response.status_code == 503
    assert response.data["checks"]["redis"] == "error"
    assert "refused" not in str(response.data)


def test_doctor(capsys, monkeypatch):
    call_command("doctor")
    assert "all good" in capsys.readouterr().out

    monkeypatch.setitem(checks.CHECKS, "redis", lambda: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(CommandError, match="redis is not reachable"):
        call_command("doctor")


def test_seed_demo_loads_once_and_reloads_on_reset(capsys):
    call_command("seed_demo", password="demo-pass-123")
    first = (Resource.objects.count(), Booking.objects.count())
    call_command("seed_demo")
    call_command("seed_demo", reset=True, password="demo-pass-123")

    assert first == (3, 4)
    assert (Resource.objects.count(), Booking.objects.count()) == first
    assert "already loaded" in capsys.readouterr().out
