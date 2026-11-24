from django.db import transaction

from webhooks.models import WebhookDelivery
from webhooks.tasks import queue_delivery


def queue_event(resource, event: str, payload: dict) -> WebhookDelivery | None:
    """Call inside the transaction that makes the change; the event commits with it."""
    if not resource.webhook_url:
        return None
    delivery = WebhookDelivery.objects.create(
        resource=resource, url=resource.webhook_url, event=event, payload=payload
    )
    transaction.on_commit(lambda: queue_delivery(delivery.pk))
    return delivery
