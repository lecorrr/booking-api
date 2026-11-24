"""A demo owner with three resources and a customer with bookings tomorrow (Kyiv time)."""

import secrets
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from bookings import services
from bookings.models import Booking
from resources.models import Resource

OWNER = "demo-owner"
CUSTOMER = "demo-customer"
KYIV = ZoneInfo("Europe/Kyiv")

# name, opens, closes, slot, min, max (minutes)
RESOURCES = [
    ("Meeting room Dnipro", time(8), time(20), 30, 30, 240),
    ("Tennis court 2", time(7), time(22), 60, 60, 120),
    ("Barber chair 1", time(10), time(19), 15, 30, 90),
]

# resource index, start, end (tomorrow, Kyiv time), confirm it?
BOOKINGS = [
    (0, time(10), time(11, 30), True),
    (0, time(14), time(15), True),
    (1, time(18), time(19), True),
    (2, time(12), time(12, 45), False),
]


class Command(BaseCommand):
    help = "Load a demo owner, three resources, a customer and a few bookings for tomorrow."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--reset", action="store_true", help="delete the demo data first")
        parser.add_argument("--password", help="password for both demo users")

    def handle(self, *args, reset: bool, password: str | None, **options) -> None:
        User = get_user_model()
        if reset:
            with transaction.atomic():
                Booking.objects.filter(resource__owner__username=OWNER).delete()
                Booking.objects.filter(customer__username=CUSTOMER).delete()
                Resource.objects.filter(owner__username=OWNER).delete()
                User.objects.filter(username__in=[OWNER, CUSTOMER]).delete()
        elif User.objects.filter(username=OWNER).exists():
            self.stdout.write("demo data is already loaded; use --reset to load it again")
            return

        password = password or secrets.token_urlsafe(9)
        with transaction.atomic():
            owner = User.objects.create_user(OWNER, f"{OWNER}@example.com", password)
            customer = User.objects.create_user(CUSTOMER, f"{CUSTOMER}@example.com", password)
            resources = [
                Resource.objects.create(
                    owner=owner,
                    name=name,
                    timezone="Europe/Kyiv",
                    opens_at=opens,
                    closes_at=closes,
                    slot_minutes=slot,
                    min_minutes=shortest,
                    max_minutes=longest,
                )
                for name, opens, closes, slot, shortest, longest in RESOURCES
            ]
        tomorrow = timezone.now().astimezone(KYIV).date() + timedelta(days=1)
        for index, start, end, confirmed in BOOKINGS:
            booking = services.hold(
                resource=resources[index],
                customer=customer,
                start=datetime.combine(tomorrow, start, tzinfo=KYIV),
                end=datetime.combine(tomorrow, end, tzinfo=KYIV),
            )
            if confirmed:
                services.confirm(booking.pk, customer=customer)

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(resources)} resources, {len(BOOKINGS)} bookings on {tomorrow:%d.%m.%Y}"
            )
        )
        self.stdout.write(f"owner: {OWNER} / {password}")
        self.stdout.write(f"customer: {CUSTOMER} / {password}")
