# booking-api

Book time slots (a meeting room, a tennis court, a barber's chair) without double bookings.
A customer holds a slot for a few minutes, then confirms it; the owner of the resource gets a
signed webhook and the customer a reminder email before the start.

Built with Django and Django REST Framework on PostgreSQL. The database itself makes double
bookings impossible: an exclusion constraint on the booked time ranges holds even when many
requests race for the same slot. Celery workers send reminders, expire holds and deliver
webhooks with retries. Everything runs locally with Docker Compose.

## Stack

| Part | Used for |
|---|---|
| Django 6.1, DRF 3.18 | API, admin, auth |
| PostgreSQL 18 | `tstzrange` periods, an exclusion constraint (`btree_gist`), row locks with `SKIP LOCKED` |
| Celery 5.7 + Redis 8 | webhook delivery with retries; beat runs expiry, reminders and clean-up every minute |
| simplejwt | JWT access and refresh tokens |
| drf-spectacular | OpenAPI schema and Swagger UI |
| pytest, factory-boy, ruff | tests (including real concurrent transactions) and linting; GitHub Actions runs them |

## Run it with Docker Compose

You need Docker with the Compose plugin. All ports are published on 127.0.0.1 only.

```bash
python3 scripts/make_env.py
docker compose up -d --build
docker compose exec api python manage.py seed_demo
```

`seed_demo` creates an owner with three resources (a meeting room, a tennis court, a barber's
chair, all in Kyiv time) and a customer with bookings for tomorrow, and prints the password of
both demo users (`demo-owner` and `demo-customer`).

| Service | Address on your machine |
|---|---|
| API and Swagger UI | http://127.0.0.1:8030/api/v1/docs/ |
| Django admin | http://127.0.0.1:8030/admin/ (create a user with `docker compose exec api python manage.py createsuperuser`) |
| PostgreSQL | 127.0.0.1:5436 |
| Redis | 127.0.0.1:6381 |

Check that everything is connected:

```bash
docker compose exec api python manage.py doctor
```

Within a minute the beat service runs the reminder task, and the worker prints the reminder
emails for tomorrow's confirmed bookings:

```bash
docker compose logs worker
```

## Try the API

Free time in the meeting room tomorrow (replace the date):

```bash
curl "http://127.0.0.1:8030/api/v1/resources/1/availability/?date=2026-11-25"
```

Log in as the demo customer (use the password `seed_demo` printed) and keep the token:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8030/api/v1/auth/token/ -H "Content-Type: application/json" -d '{"username": "demo-customer", "password": "PASTE-PASSWORD"}' | python3 -c "import json, sys; print(json.load(sys.stdin)['access'])")
```

Hold a slot. Send the same command twice: the second answer is the first one replayed
(`Idempotent-Replayed: true`), not a second booking.

```bash
curl -i -X POST http://127.0.0.1:8030/api/v1/bookings/ -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: my-first-booking-1" -d '{"resource": 1, "start": "2026-11-25T16:00:00+02:00", "end": "2026-11-25T17:00:00+02:00"}'
```

Confirm it within 10 minutes (use the `id` from the answer):

```bash
curl -X POST http://127.0.0.1:8030/api/v1/bookings/5/confirm/ -H "Authorization: Bearer $TOKEN"
```

Try to book an overlapping time and get `409 Conflict`; cancel with
`POST /api/v1/bookings/5/cancel/`.

## How a booking moves

```
            confirm (within 10 min)
  held  -------------------------->  confirmed
   |  \                                  |
   |   \ not confirmed in time           | cancel (customer: 2 h notice; owner: any time)
   |    `-----------> expired            v
   `------ cancel ----------------->  cancelled
```

Only `held` and `confirmed` bookings occupy the slot. Times are wall-clock times of the
resource's time zone; requests must carry a UTC offset (`2026-11-25T16:00:00+02:00`), and
answers show times in the resource's zone.

## Endpoints

All under `/api/v1/`. The full schema is at `/api/v1/schema/`.

| Endpoint | Who | What |
|---|---|---|
| `GET resources/` | anyone | active resources |
| `GET resources/{id}/availability/?date=` | anyone | free stretches of that day, long enough to book |
| `POST resources/` | logged in | create a resource; the creator owns it |
| `PATCH`, `DELETE resources/{id}/` | owner | change the rules or retire the resource |
| `GET resources/mine/` | logged in | your resources, retired ones included |
| `GET resources/{id}/bookings/?start_from=&start_to=` | owner | every booking of the resource |
| `GET resources/{id}/webhook-secret/`, `POST .../rotate-webhook-secret/` | owner | the key that signs your webhooks |
| `POST bookings/` | logged in | hold a slot; optional `Idempotency-Key` header |
| `POST bookings/{id}/confirm/` | customer | turn the hold into a booking |
| `POST bookings/{id}/cancel/` | customer or owner | cancel |
| `GET bookings/`, `bookings/{id}/` | customer (owner for details) | your bookings |
| `POST auth/register/`, `auth/token/`, `auth/token/refresh/` | anyone | sign up and get JWT tokens |
| `GET health/` | anyone | 200 when PostgreSQL and Redis answer, otherwise 503 |

## Webhooks

Set `webhook_url` (https only) on a resource to receive `booking.confirmed` and
`booking.cancelled` events. Each delivery is a JSON `POST`:

```json
{"id": 12, "event": "booking.confirmed", "created_at": "...", "data": {"booking_id": 5, "resource_id": 1, "status": "confirmed", "start": "...", "end": "...", "customer": "demo-customer", "note": ""}}
```

with the headers `X-Booking-Event`, `X-Booking-Delivery` (the same id on every retry, so you
can ignore repeats) and `X-Booking-Signature: t=<unix time>,v1=<hex>`. The signature is
HMAC-SHA256 of `"<t>.<raw body>"` with the resource's webhook secret. A receiver checks it
like this (the same function the tests use, `webhooks/signing.py`):

```python
import time

from webhooks.signing import verify

ok = verify(secret, raw_body, request.headers["X-Booking-Signature"], now=int(time.time()))
```

Deliveries that fail with a network error, a 429 or a 5xx are retried 6 times over about half
an hour; other answers fail at once. To watch deliveries while developing, point
`webhook_url` at a request inspector such as webhook.site.

## Developing without rebuilding the image

Run PostgreSQL and Redis in Compose and Django from a virtual environment (Python 3.12 or
newer):

```bash
docker compose up -d postgres redis
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

The worker and beat, each in its own terminal:

```bash
.venv/bin/celery -A bookingapi worker --loglevel=INFO
```

```bash
.venv/bin/celery -A bookingapi beat --loglevel=INFO --schedule=/tmp/celerybeat-schedule
```

## Tests

The tests use the real PostgreSQL from Compose (the database `test_bookingapi`), because the
exclusion constraint, range lookups and row locks are what is being tested:

```bash
docker compose up -d postgres redis
.venv/bin/pytest
```

`tests/test_concurrency.py` starts several threads with their own database connections that
try to book the same slot at the same moment; exactly one of them wins.

Before pushing, run what CI runs:

```bash
.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/python manage.py makemigrations --check --dry-run && .venv/bin/pytest
```

## Configuration

Settings come from environment variables (or `.env`, see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | required | signing key for sessions and JWT |
| `DJANGO_DEBUG` | `false` | never `true` on a public server |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | host names the API answers to |
| `DATABASE_URL` | required | PostgreSQL URL |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery broker |
| `CACHE_URL` | in-process memory | use Redis when there is more than one process |
| `BOOKING_HOLD_MINUTES` | `10` | how long a hold waits for confirmation |
| `BOOKING_CANCEL_NOTICE_HOURS` | `2` | customers cannot cancel later than this before the start |
| `BOOKING_REMINDER_HOURS` | `24` | reminders go out this long before the start |
| `BOOKING_MAX_DAYS_AHEAD` | `90` | how far ahead bookings open |
| `MAILER_BACKEND`, `SMTP_*` | console | how reminders are sent |
| `THROTTLE_ANON`, `THROTTLE_USER`, `THROTTLE_REGISTER`, `THROTTLE_BOOKINGS` | `60/min`, `240/min`, `10/hour`, `30/min` | request limits |
| `WEB_CONCURRENCY` | `1` | gunicorn worker processes |

## Layout

```
bookingapi/   settings, URLs, the Celery app and its schedule
accounts/     user model, registration, JWT login
resources/    what can be booked, its rules and availability endpoint
bookings/     periods.py (pure rules), services.py (every change), idempotency, tasks
webhooks/     the outbox model, signing, safe delivery, retry tasks
core/         health checks, pagination, management commands
tests/        pytest suite, one file per area
```

How the parts fit together is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Updating dependencies

`requirements.lock` (image) and `requirements-dev.lock` (development and CI) pin exact
versions. After changing `pyproject.toml`, regenerate them with
[uv](https://docs.astral.sh/uv/):

```bash
uv pip compile pyproject.toml --python-version 3.14 -o requirements.lock
uv pip compile pyproject.toml --extra dev --python-version 3.14 -o requirements-dev.lock
```
