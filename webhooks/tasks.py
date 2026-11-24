import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from webhooks.delivery import DeliveryError, post_delivery
from webhooks.models import WebhookDelivery

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 7
# A pending delivery this far past its due time has lost its task (Redis or a worker died).
LOST_AFTER = timedelta(minutes=10)
REQUEUE_BATCH = 500


def retry_delay_s(attempts: int) -> int:
    """30 s after the first failure, doubling, at most 30 minutes."""
    return min(30 * 2 ** (attempts - 1), 1800)


@shared_task(bind=True, max_retries=MAX_ATTEMPTS)
def deliver_webhook(self, delivery_id: int) -> str:
    retry_in = None
    with transaction.atomic():
        # SKIP LOCKED: if a requeued copy of this task is already sending, leave it to that one.
        delivery = (
            WebhookDelivery.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("resource")
            .filter(pk=delivery_id)
            .first()
        )
        if delivery is None:
            return "skipped"
        if delivery.status != WebhookDelivery.Status.PENDING:
            return delivery.status
        delivery.attempts += 1
        try:
            post_delivery(delivery)
        except DeliveryError as error:
            delivery.last_error = str(error)[:300]
            if error.transient and delivery.attempts < MAX_ATTEMPTS:
                retry_in = retry_delay_s(delivery.attempts)
                delivery.next_attempt_at = timezone.now() + timedelta(seconds=retry_in)
            else:
                delivery.status = WebhookDelivery.Status.FAILED
                log.warning("delivery=%s failed: %s", delivery.pk, error)
        except Exception:
            log.exception("delivery=%s crashed", delivery.pk)
            delivery.status = WebhookDelivery.Status.FAILED
            delivery.last_error = "internal error"
        else:
            delivery.status = WebhookDelivery.Status.DELIVERED
            delivery.delivered_at = timezone.now()
            delivery.last_error = ""
        delivery.save()
    if retry_in is not None:
        raise self.retry(countdown=retry_in)
    return delivery.status


def queue_delivery(delivery_id: int) -> None:
    try:
        deliver_webhook.delay(delivery_id)
    except OperationalError as error:
        # The row stays pending; requeue_lost_deliveries picks it up later.
        log.error("delivery=%s not queued: %s", delivery_id, error)


@shared_task
def requeue_lost_deliveries() -> int:
    lost = list(
        WebhookDelivery.objects.filter(
            status=WebhookDelivery.Status.PENDING, next_attempt_at__lt=timezone.now() - LOST_AFTER
        ).values_list("pk", flat=True)[:REQUEUE_BATCH]
    )
    for delivery_id in lost:
        queue_delivery(delivery_id)
    return len(lost)
