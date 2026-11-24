import json
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from bookings import services
from webhooks import tasks
from webhooks.delivery import DeliveryError, check_url, post_delivery
from webhooks.models import WebhookDelivery
from webhooks.signing import HEADER, sign, verify
from webhooks.tasks import MAX_ATTEMPTS

HOOK_URL = "https://hooks.example.com/bookings"
PUBLIC = "93.184.216.34"
SECRET = "s3cret"


def public_dns(host, port):
    return [PUBLIC]


# Signing: what a receiver checks.


def test_a_signature_verifies():
    body = b'{"event": "booking.confirmed"}'

    assert verify(SECRET, body, sign(SECRET, body, 1_800_000_000), now=1_800_000_010)


@pytest.mark.parametrize(
    "secret, body, now",
    [
        ("wrong", b"{}", 1_800_000_000),
        (SECRET, b'{"changed": 1}', 1_800_000_000),
        (SECRET, b"{}", 1_800_000_000 + 301),  # replayed later
    ],
)
def test_a_signature_fails(secret, body, now):
    header = sign(SECRET, b"{}", 1_800_000_000)

    assert not verify(secret, body, header, now=now)


@pytest.mark.parametrize("header", ["", "t=abc,v1=00", "v1=00", "garbage"])
def test_a_malformed_header_fails(header):
    assert not verify(SECRET, b"{}", header, now=1_800_000_000)


# Outbox: events are written with the booking change and only if it commits.


@pytest.fixture
def hooked_resource(resource):
    resource.webhook_url = HOOK_URL
    resource.save()
    return resource


@pytest.fixture
def queued(monkeypatch):
    sent = []
    monkeypatch.setattr(tasks.deliver_webhook, "delay", sent.append)
    return sent


@pytest.mark.django_db
def test_confirming_queues_an_event_after_commit(
    hooked_resource, user, book, queued, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        booking = book(hooked_resource, user, 10)

    delivery = WebhookDelivery.objects.get()
    assert delivery.event == "booking.confirmed"
    assert delivery.payload["booking_id"] == booking.pk
    assert queued == [delivery.pk]


@pytest.mark.django_db
def test_only_cancelling_a_confirmed_booking_is_an_event(hooked_resource, user, book, queued):
    held = book(hooked_resource, user, 10, confirm=False)
    confirmed = book(hooked_resource, user, 12)

    services.cancel(held.pk, user=user)
    services.cancel(confirmed.pk, user=user)

    events = list(WebhookDelivery.objects.values_list("event", "payload__booking_id"))
    assert events == [("booking.confirmed", confirmed.pk), ("booking.cancelled", confirmed.pk)]


@pytest.mark.django_db
def test_no_url_no_event(resource, user, book, queued):
    book(resource, user, 10)

    assert not WebhookDelivery.objects.exists()


# Delivery.


def delivery_for(resource) -> WebhookDelivery:
    return WebhookDelivery.objects.create(
        resource=resource, url=HOOK_URL, event="booking.confirmed", payload={"booking_id": 7}
    )


@pytest.mark.django_db
def test_delivery_is_signed_with_the_resource_secret(hooked_resource):
    delivery = delivery_for(hooked_resource)
    seen = []

    def receiver(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    post_delivery(
        delivery,
        client=httpx.Client(transport=httpx.MockTransport(receiver)),
        resolver=public_dns,
        clock=lambda: 1_800_000_000,
    )

    request = seen[0]
    assert request.headers["X-Booking-Delivery"] == str(delivery.pk)
    assert verify(
        hooked_resource.webhook_secret, request.content, request.headers[HEADER], 1_800_000_000
    )
    assert json.loads(request.content)["data"] == {"booking_id": 7}


@pytest.mark.parametrize(
    "url, address",
    [
        ("http://hooks.example.com/b", PUBLIC),
        ("https://hooks.example.com/b", "127.0.0.1"),
        ("https://hooks.example.com/b", "169.254.169.254"),
        ("https://hooks.example.com/b", "10.1.2.3"),
    ],
)
def test_webhooks_never_go_to_internal_addresses(url, address):
    with pytest.raises(DeliveryError) as error:
        check_url(url, lambda host, port: [address])
    assert not error.value.transient


def answering(status: int):
    def fake_post(delivery):
        if status >= 400:
            transient = status == 429 or status >= 500
            raise DeliveryError(f"the receiver answered {status}", transient=transient)

    return fake_post


@pytest.mark.django_db
def test_delivered(hooked_resource, monkeypatch):
    monkeypatch.setattr(tasks, "post_delivery", answering(200))
    delivery = delivery_for(hooked_resource)

    assert tasks.deliver_webhook.delay(delivery.pk).get() == "delivered"
    delivery.refresh_from_db()
    assert (delivery.status, delivery.attempts) == ("delivered", 1)
    assert delivery.delivered_at is not None


@pytest.mark.django_db
def test_a_receiver_error_is_retried_until_the_last_attempt(hooked_resource, monkeypatch):
    monkeypatch.setattr(tasks, "post_delivery", answering(503))
    monkeypatch.setitem(tasks.deliver_webhook.app.conf, "task_eager_propagates", False)
    delivery = delivery_for(hooked_resource)

    tasks.deliver_webhook.delay(delivery.pk)

    delivery.refresh_from_db()
    assert (delivery.status, delivery.attempts) == ("failed", MAX_ATTEMPTS)
    assert "503" in delivery.last_error


@pytest.mark.django_db
def test_a_refusal_is_not_retried(hooked_resource, monkeypatch):
    monkeypatch.setattr(tasks, "post_delivery", answering(410))
    delivery = delivery_for(hooked_resource)

    assert tasks.deliver_webhook.delay(delivery.pk).get() == "failed"
    delivery.refresh_from_db()
    assert delivery.attempts == 1


@pytest.mark.django_db
def test_a_finished_delivery_is_not_sent_again(hooked_resource, monkeypatch):
    def must_not_send(delivery):
        raise AssertionError("sent twice")

    monkeypatch.setattr(tasks, "post_delivery", must_not_send)
    delivery = delivery_for(hooked_resource)
    WebhookDelivery.objects.filter(pk=delivery.pk).update(status="delivered")

    assert tasks.deliver_webhook.delay(delivery.pk).get() == "delivered"


@pytest.mark.django_db
def test_lost_deliveries_are_queued_again(hooked_resource, queued):
    lost = delivery_for(hooked_resource)
    WebhookDelivery.objects.filter(pk=lost.pk).update(
        next_attempt_at=timezone.now() - timedelta(minutes=11)
    )
    delivery_for(hooked_resource)  # just created, its task is still on the way

    assert tasks.requeue_lost_deliveries.delay().get() == 1
    assert queued == [lost.pk]


def test_retry_delay_grows_and_is_capped():
    assert [tasks.retry_delay_s(attempt) for attempt in range(1, 9)] == [
        30,
        60,
        120,
        240,
        480,
        960,
        1800,
        1800,
    ]
