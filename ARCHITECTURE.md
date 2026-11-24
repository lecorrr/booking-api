# Architecture

## Overview

```
            HTTP (JSON, JWT)
                  |
          +-------v--------+   one transaction per change    +--------------+
          |  Django + DRF  |-------------------------------->|  PostgreSQL  |
          |  (gunicorn)    |   booking row + outbox row      |              |
          +-------+--------+                                 +------^-------+
                  | after commit: queue the delivery               |
                  v                                                |
              +-------+     +----------------+  reads, locks       |
              | Redis |---->| Celery worker  |---------------------+
              +---^---+     +-------+--------+
                  |                 |
          +-------+-------+         +----> owner's webhook URL (https only), reminder email
          |  Celery beat  |  every minute: expire holds, send reminders;
          +---------------+  every 5 minutes: requeue lost webhook deliveries
```

The API, the worker and beat are the same code base and the same Docker image, started with
different commands. Exactly one beat process runs.

## Apps

| App | Owns | May use |
|---|---|---|
| `accounts` | the user model, registration, JWT endpoints | |
| `resources` | what can be booked, its rules, its webhook settings | `accounts` |
| `bookings` | bookings, idempotency keys, the booking rules, every state change, periodic tasks, availability | `resources`, `webhooks` |
| `webhooks` | the outbox table, signing, delivery and its retries | `resources` |
| `core` | health checks, pagination, management commands | all of the above |

`bookings/periods.py` (the rules) and `webhooks/signing.py` import no framework, so they are
tested as plain functions. Views never save a booking themselves; every change goes through
`bookings/services.py`. `tests/test_boundaries.py` checks both.

## No double bookings

The rule "two active bookings of one resource never overlap" is a constraint in the database,
not a check in Python:

```sql
EXCLUDE USING gist (resource_id WITH =, period WITH &&)
WHERE (status IN ('held', 'confirmed'))
```

`period` is a `tstzrange` with `[start, end)` bounds, so 10:00-11:00 and 11:00-12:00 do not
overlap. `btree_gist` is needed so the GiST index can compare `resource_id` with `=`; the
first migration creates the extension.

Checking in Python first ("is anything there? no, then insert") has a race: two requests both
see a free slot, then both insert. With the constraint the second `INSERT` waits for the first
transaction, then fails with an exclusion violation, which the service turns into
`409 Conflict` by its constraint name (`booking_no_overlap`). `tests/test_concurrency.py`
runs eight threads, each with its own connection, against the same slot at the same moment and
expects exactly one booking.

Other rules the database enforces: a period has both ends (`booking_period_bounded`), a hold
has a deadline (`booking_hold_has_deadline`), a resource closes after it opens and its minimum
length is not above its maximum.

## Holds, expiry and availability

A new booking is `held` for `BOOKING_HOLD_MINUTES` (10) and occupies the slot like a
confirmed one, so nobody can take it while the customer is paying or thinking. Beat expires
old holds every minute with one `UPDATE`. A hold that ran out less than a minute ago would
still block the slot, so creating a booking first expires the stale holds of that resource in
the same transaction, and availability ignores holds past their deadline.

Confirming locks the booking row (`SELECT ... FOR UPDATE OF booking`, so the resource and user
rows joined for the response are not locked too). Confirming twice returns the booking again;
confirming after the deadline marks it expired and answers 409.

## Time zones

Each resource has an IANA time zone. Opening hours and slot boundaries are wall-clock times in
that zone ("09:00 to 18:00 every day, in 15-minute steps"); durations and comparisons are done
in UTC. That distinction matters twice a year: Python ignores the offsets of two datetimes
that share a `tzinfo`, so on the day the clocks go forward 02:00 to 05:00 would count as three
hours although only two pass. A test checks exactly that night.

Requests must carry a UTC offset; a time without one is refused rather than guessed. Answers
show times in the resource's zone. The `tzdata` package is a dependency, so the set of valid
zone names does not depend on what the Docker base image ships.

## Idempotency keys

Mobile clients retry when a response is lost, and a retried `POST /bookings/` must not create
a second booking. With an `Idempotency-Key` header the first successful response is stored
with the key (per user) in the same transaction as the booking:

- the same key and the same body get the stored response back, with `Idempotent-Replayed: true`;
- the same key with a different body is `422`;
- a failed request (409, 400) stores nothing, so the client can retry it;
- if two requests with one key arrive at once, the second waits on the unique index until the
  first commits, then replays its answer (also covered by a concurrency test);
- records older than a day are deleted by a beat task.

## Reminders

Beat runs `send_reminders` every minute. It selects confirmed bookings that start within
`BOOKING_REMINDER_HOURS` and have no `reminder_sent_at`, with `FOR UPDATE SKIP LOCKED`, so two
overlapping runs (a slow mail server, two workers) take different rows instead of sending
twice. A failed send leaves the row for the next run.

Scheduling one Celery task per booking with an ETA a day ahead would be simpler to write but
fragile with Redis as the broker: tasks whose ETA is further away than the visibility timeout
(one hour by default) get delivered again, and an ETA task lives only in the broker. A
periodic query over the table has neither problem.

## Webhooks

Owners can set a `webhook_url` and receive `booking.confirmed` and `booking.cancelled`.

- **Outbox.** The event row (`WebhookDelivery`) is written in the transaction that changes the
  booking. The Celery task is queued only after commit (`transaction.on_commit`). A rolled-back
  change sends nothing; a queue that is down loses nothing, because beat requeues pending
  deliveries whose next attempt is more than 10 minutes overdue.
- **Signing.** `X-Booking-Signature: t=<unix time>,v1=<HMAC-SHA256 of "t.body">` with a
  per-resource secret that the owner can read and rotate. Signing the timestamp lets
  receivers refuse old, replayed requests; comparison is constant-time.
- **Delivery.** https only, the host must resolve to public addresses (no localhost, private
  networks or the cloud metadata service), no redirects, 5-second timeout.
- **Retries.** Network errors, 429 and 5xx are retried with doubling delays from 30 seconds,
  7 attempts at most, counted in the database so a requeued task does not start over. Other
  answers fail at once. The worker locks the delivery row with `SKIP LOCKED` while sending, so
  a requeued copy of the task does not send in parallel.
- **At least once.** A receiver can still see an event twice (for example if it answered
  after our timeout). `X-Booking-Delivery` stays the same on every attempt so receivers can
  ignore repeats.

## Security notes

- JWT access tokens live 30 minutes, refresh tokens 7 days.
- Another owner's resource or another customer's booking answers 404, not 403.
- Throttling: anonymous, per user, registration and booking creation have separate limits.
  With more than one process the cache (and so the counters) must be Redis, which is how
  Compose runs it.
- The webhook URL and secret are shown to the owner only.
- Secrets come from the environment; `.env` is never committed or copied into the image.

## Known limits and next steps

- Opening hours are the same every day; no closed days, breaks or holidays yet.
- No payments: a hold is where a payment step would go.
- The SSRF check resolves the webhook host before connecting and httpx resolves it again; a
  DNS server that answers differently the second time could slip past it. Pinning the checked
  address for the connection would close that.
- Reminders query `lower(period)` without an index of its own; at a few thousand bookings a
  day that is fine, beyond that an expression index would help.

## Adding things

- **A new event:** call `queue_event(resource, "booking.something", payload)` inside the
  service function's transaction.
- **A new periodic job:** a `@shared_task` in the app's `tasks.py` and an entry in
  `CELERY_BEAT_SCHEDULE`.
- **A booking rule that needs no database:** a check in `bookings/periods.py`, with a
  parametrised case in `tests/test_periods.py`.
