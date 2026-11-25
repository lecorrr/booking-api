# Changelog

## 0.1.2 (2026-11-25)

- README: the live demo on AWS, with an availability query anyone can run without an
  account.

## 0.1.1 (2026-11-25)

- `compose.prod.yaml` with settings for a 2 GB server.
- `scripts/make_env.py --host NAME` writes production settings for that host name.
- The version is defined once, in the package; the OpenAPI schema and `pyproject.toml`
  read it from there.

## 0.1.0 (2026-11-24)

First version.

- Resources with an owner, a time zone, daily opening hours, a slot size and minimum and
  maximum booking length; availability per day.
- Bookings: hold for 10 minutes, confirm, cancel (customers with 2 hours' notice, owners at
  any time). A PostgreSQL exclusion constraint makes overlapping active bookings impossible.
- `Idempotency-Key` support on creating a booking.
- Celery beat tasks: expire holds, send reminder emails (`SKIP LOCKED`), purge old
  idempotency keys, requeue lost webhook deliveries.
- Webhooks for `booking.confirmed` and `booking.cancelled` through an outbox table, signed
  with HMAC-SHA256, retried with backoff, never sent to internal addresses.
- JWT authentication, throttling, OpenAPI schema and Swagger UI.
- Docker Compose for local development (API, worker, beat), GitHub Actions CI, `doctor` and
  `seed_demo` management commands.
