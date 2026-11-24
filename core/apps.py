import logging

from django.apps import AppConfig

log = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        from bookingapi import __version__

        log.info("booking-api %s", __version__)
