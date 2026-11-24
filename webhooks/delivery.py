"""Posting one event to a resource owner's URL.

Owners type the URL in, so the worker must not be usable to reach its own network (SSRF):
only https to public addresses, no redirects, short timeouts.
"""

import ipaddress
import json
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from webhooks.models import WebhookDelivery
from webhooks.signing import HEADER, sign

TIMEOUT = httpx.Timeout(5.0, connect=3.0)
USER_AGENT = "booking-api webhooks"

Resolver = Callable[[str, int], list[str]]


class DeliveryError(Exception):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


def resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def check_url(url: str, resolver: Resolver = resolve) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise DeliveryError("the webhook URL must be https:// with a host", transient=False)
    try:
        addresses = resolver(parts.hostname, parts.port or 443)
    except OSError as error:
        raise DeliveryError(f"cannot resolve {parts.hostname}", transient=True) from error
    if not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise DeliveryError(f"{parts.hostname} is not a public address", transient=False)


def build_body(delivery: WebhookDelivery) -> bytes:
    event = {
        "id": delivery.pk,
        "event": delivery.event,
        "created_at": delivery.created_at.isoformat(),
        "data": delivery.payload,
    }
    return json.dumps(event, separators=(",", ":")).encode()


def post_delivery(
    delivery: WebhookDelivery,
    *,
    client: httpx.Client | None = None,
    resolver: Resolver = resolve,
    clock: Callable[[], float] = time.time,
) -> None:
    check_url(delivery.url, resolver)
    body = build_body(delivery)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-Booking-Event": delivery.event,
        # Receivers use this to ignore an event they already processed.
        "X-Booking-Delivery": str(delivery.pk),
        HEADER: sign(delivery.resource.webhook_secret, body, int(clock())),
    }
    http = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    try:
        with http:
            response = http.post(delivery.url, content=body, headers=headers)
    except httpx.TransportError as error:
        raise DeliveryError(f"request failed: {type(error).__name__}", transient=True) from error
    if response.is_success:
        return
    transient = response.status_code == 429 or response.status_code >= 500
    raise DeliveryError(f"the receiver answered {response.status_code}", transient=transient)
