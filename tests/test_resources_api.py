import pytest

from resources.models import Resource
from tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db

RESOURCES = "/api/v1/resources/"


def new_resource(**fields) -> dict:
    return {"name": "Meeting room", "opens_at": "09:00", "closes_at": "18:00"} | fields


def test_the_creator_owns_the_resource(user_api, user):
    response = user_api.post(RESOURCES, new_resource(), format="json")

    assert response.status_code == 201
    resource = Resource.objects.get(pk=response.data["id"])
    assert resource.owner == user
    assert len(resource.webhook_secret) == 64


def test_creating_needs_a_login(api):
    assert api.post(RESOURCES, new_resource(), format="json").status_code == 401


@pytest.mark.parametrize(
    "fields, field",
    [
        ({"opens_at": "18:00", "closes_at": "09:00"}, "closes_at"),
        ({"min_minutes": 120, "max_minutes": 60}, "min_minutes"),
        ({"slot_minutes": 30, "min_minutes": 45}, "min_minutes"),
        ({"slot_minutes": 7}, "slot_minutes"),
        ({"opens_at": "09:00", "closes_at": "09:30", "min_minutes": 60}, "min_minutes"),
        ({"timezone": "Europe/Atlantis"}, "timezone"),
        ({"webhook_url": "http://hooks.example.com/bookings"}, "webhook_url"),
    ],
)
def test_invalid_resource_is_rejected(user_api, fields, field):
    response = user_api.post(RESOURCES, new_resource(**fields), format="json")

    assert response.status_code == 400
    assert field in response.data


def test_patch_is_checked_against_the_stored_values(owner_api, resource):
    response = owner_api.patch(f"{RESOURCES}{resource.pk}/", {"closes_at": "07:00"}, format="json")

    assert response.status_code == 400  # the resource opens at 08:00


def test_only_the_owner_changes_a_resource(user_api, resource):
    response = user_api.patch(f"{RESOURCES}{resource.pk}/", {"name": "Mine"}, format="json")

    assert response.status_code == 404


def test_deleting_retires_the_resource(owner_api, api, resource):
    assert owner_api.delete(f"{RESOURCES}{resource.pk}/").status_code == 204

    resource.refresh_from_db()
    assert not resource.is_active
    assert api.get(f"{RESOURCES}{resource.pk}/").status_code == 404


def test_webhook_url_is_shown_to_the_owner_only(owner_api, api, resource):
    resource.webhook_url = "https://hooks.example.com/bookings"
    resource.save()

    assert owner_api.get(f"{RESOURCES}{resource.pk}/").data["webhook_url"]
    assert "webhook_url" not in api.get(f"{RESOURCES}{resource.pk}/").data


def test_webhook_secret_is_for_the_owner_and_can_be_rotated(owner_api, user_api, resource):
    url = f"{RESOURCES}{resource.pk}/"

    first = owner_api.get(f"{url}webhook-secret/").data["secret"]
    rotated = owner_api.post(f"{url}rotate-webhook-secret/").data["secret"]

    assert first == resource.webhook_secret
    assert rotated != first
    assert user_api.get(f"{url}webhook-secret/").status_code == 404
    assert user_api.post(f"{url}rotate-webhook-secret/").status_code == 404


def test_mine_lists_own_resources_including_retired(owner_api, resource):
    ResourceFactory()
    retired = ResourceFactory(owner=resource.owner, is_active=False)

    ids = [row["id"] for row in owner_api.get(f"{RESOURCES}mine/").data["results"]]

    assert ids == [resource.pk, retired.pk]


def test_owner_lists_the_bookings_of_a_resource(owner_api, user_api, resource, user, book):
    early, late = book(resource, user, 9), book(resource, user, 15)

    everything = owner_api.get(f"{RESOURCES}{resource.pk}/bookings/")
    from_noon = owner_api.get(
        f"{RESOURCES}{resource.pk}/bookings/", {"start_from": late.start.isoformat()}
    )

    assert [row["id"] for row in everything.data["results"]] == [early.pk, late.pk]
    assert [row["id"] for row in from_noon.data["results"]] == [late.pk]
    assert user_api.get(f"{RESOURCES}{resource.pk}/bookings/").status_code == 404
