from django.core.mail import send_mail

from bookings.models import Booking


def send_reminder_email(booking: Booking) -> None:
    start = booking.start.astimezone(booking.resource.zone)
    end = booking.end.astimezone(booking.resource.zone)
    send_mail(
        subject=f"Reminder: {booking.resource.name} on {start:%d.%m.%Y} at {start:%H:%M}",
        message=(
            f"Hello {booking.customer.username},\n\n"
            f"your booking of {booking.resource.name} is on {start:%d.%m.%Y} "
            f"from {start:%H:%M} to {end:%H:%M} ({booking.resource.timezone}).\n"
        ),
        from_email=None,
        recipient_list=[booking.customer.email],
    )
