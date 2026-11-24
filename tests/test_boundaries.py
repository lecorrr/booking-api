"""Rules about which code may use what, checked here so they do not erode."""

import ast
from pathlib import Path

from django.conf import settings

ROOT = Path(__file__).resolve().parent.parent
APPS = ["accounts", "resources", "bookings", "webhooks", "core"]
FRAMEWORKS = {"django", "rest_framework", "celery", "httpx"}


def imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_booking_rules_and_signing_have_no_framework_dependencies():
    for name in ["bookings/periods.py", "webhooks/signing.py"]:
        assert not imported_roots(ROOT / name) & FRAMEWORKS, name


def test_views_change_bookings_only_through_services():
    source = (ROOT / "bookings/views.py").read_text()

    assert ".save(" not in source
    assert "objects.create(" not in source


def test_every_app_is_installed():
    assert set(APPS) <= set(settings.INSTALLED_APPS)


def test_dependencies_point_one_way():
    # resources <- webhooks <- bookings: nothing below may import what is above it.
    forbidden = {"resources": {"bookings", "webhooks"}, "webhooks": {"bookings"}}
    for app, above in forbidden.items():
        for path in (ROOT / app).rglob("*.py"):
            if "migrations" not in path.parts:
                assert not imported_roots(path) & above, path.relative_to(ROOT)
