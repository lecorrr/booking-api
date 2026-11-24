"""HMAC-SHA256 signatures for webhook bodies.

The header is  t=<unix seconds>,v1=<hex HMAC of "<t>.<body>">  with the resource's secret.
The timestamp is part of what is signed, so a receiver can refuse old, replayed requests.
"""

import hashlib
import hmac

HEADER = "X-Booking-Signature"
DEFAULT_TOLERANCE_S = 300


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def verify(
    secret: str, body: bytes, header: str, now: int, tolerance_s: int = DEFAULT_TOLERANCE_S
) -> bool:
    """What a receiver runs: right secret, unchanged body, recent enough."""
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        timestamp = int(fields["t"])
    except (KeyError, ValueError):
        return False
    if abs(now - timestamp) > tolerance_s:
        return False
    expected = sign(secret, body, timestamp)
    return hmac.compare_digest(expected, f"t={timestamp},v1={fields.get('v1', '')}")
