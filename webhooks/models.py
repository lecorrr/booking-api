from django.db import models
from django.utils import timezone

from resources.models import Resource


class WebhookDelivery(models.Model):
    """One event for one resource owner's URL.

    The row is written in the same transaction as the booking change it reports (an
    outbox), so an event is never lost if the queue is down and never sent for a change
    that was rolled back.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        DELIVERED = "delivered"
        FAILED = "failed"

    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="webhook_deliveries"
    )
    url = models.URLField(max_length=500)
    event = models.CharField(max_length=50)
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    # When the next attempt is due; a pending delivery long past it has lost its task.
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="delivery_due_idx")]

    def __str__(self) -> str:
        return f"{self.event} #{self.pk} ({self.status})"
