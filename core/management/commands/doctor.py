from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from bookingapi import __version__
from core.checks import run_checks


def describe_database() -> str:
    db = settings.DATABASES["default"]
    return f"{db['USER']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"


def describe_redis() -> str:
    parts = urlsplit(settings.REDIS_URL)
    return f"{parts.hostname}:{parts.port or 6379}{parts.path}"


def pending_migrations() -> list[str]:
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _ in plan]


class Command(BaseCommand):
    help = "Print the settings that matter and check every dependency. Exits 1 on a problem."

    def handle(self, *args, **options) -> None:
        self.stdout.write(f"booking-api {__version__}")
        self.stdout.write(f"  debug       {settings.DEBUG}")
        self.stdout.write(f"  hosts       {', '.join(settings.ALLOWED_HOSTS)}")
        self.stdout.write(f"  postgres    {describe_database()}")
        self.stdout.write(f"  redis       {describe_redis()}")
        self.stdout.write(f"  email       {settings.MAILERS['default']['BACKEND'].split('.')[-2]}")

        problems = []
        for name, result in run_checks().items():
            self.stdout.write(f"  {name:<11} {result}")
            if result != "ok":
                problems.append(f"{name} is not reachable")
        if "postgres is not reachable" not in problems:
            pending = pending_migrations()
            self.stdout.write(f"  migrations  {len(pending)} pending")
            if pending:
                problems.append(f"run migrate: {', '.join(pending)}")

        if problems:
            raise CommandError("; ".join(problems))
        self.stdout.write(self.style.SUCCESS("all good"))
