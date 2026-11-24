"""Idempotency keys: a client may resend a request and get the first answer, not a second booking.

Phones on bad connections retry. With an Idempotency-Key header, the first successful response
is stored with the key, and a repeat of the same request gets it back. Failed requests are
not stored, so they can be retried.
"""

import hashlib
import json
import re
from collections.abc import Callable

from django.db import transaction

from bookings.models import IdempotencyRecord

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,100}$")

Handler = Callable[[], tuple[int, dict]]


class KeyReused(Exception):
    """The key came back with a different request body."""


def request_fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def run_once(*, user, key: str, fingerprint: str, handler: Handler) -> tuple[int, dict, bool]:
    """Returns (status, body, replayed)."""
    with transaction.atomic():
        # A concurrent request with the same key blocks on the unique index here until the
        # first one commits, and then finds its stored response.
        record, created = IdempotencyRecord.objects.get_or_create(
            user=user,
            key=key,
            defaults={"request_hash": fingerprint, "response_status": 0, "response_body": {}},
        )
        if not created:
            if record.request_hash != fingerprint:
                raise KeyReused(key)
            return record.response_status, record.response_body, True
        status, body = handler()  # an error rolls the record back with everything else
        record.response_status = status
        record.response_body = body
        record.save(update_fields=["response_status", "response_body"])
        return status, body, False
