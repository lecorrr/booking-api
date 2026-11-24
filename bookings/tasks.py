from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from bookings import services
from bookings.models import IdempotencyRecord
from bookings.notifications import send_reminder_email


@shared_task
def expire_holds() -> int:
    return services.expire_holds()


@shared_task
def send_reminders() -> int:
    return services.send_due_reminders(send=send_reminder_email)


@shared_task
def purge_idempotency_records() -> int:
    cutoff = timezone.now() - timedelta(hours=settings.IDEMPOTENCY_KEEP_HOURS)
    deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
    return deleted
