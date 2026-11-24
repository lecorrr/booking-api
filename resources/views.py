from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from resources.models import Resource, new_webhook_secret
from resources.permissions import IsOwnerOrReadOnly
from resources.serializers import (
    ResourceSerializer,
    WebhookSecretSerializer,
)

OWNER_ACTIONS = {"webhook_secret", "rotate_webhook_secret", "bookings"}


@extend_schema_view(mine=extend_schema(responses=ResourceSerializer(many=True)))
class ResourceViewSet(viewsets.ModelViewSet):
    serializer_class = ResourceSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    lookup_value_regex = "[0-9]+"

    def get_queryset(self):
        user_id = self.request.user.id
        if self.request.method not in permissions.SAFE_METHODS or self.action in OWNER_ACTIONS:
            # Someone else's resource is a 404 here, not a 403.
            return Resource.objects.filter(owner_id=user_id).select_related("owner")
        return Resource.objects.filter(is_active=True).select_related("owner")

    def perform_create(self, serializer) -> None:
        serializer.save(owner=self.request.user)

    def destroy(self, request: Request, *args, **kwargs) -> Response:
        """Retired, not deleted: past bookings keep pointing at it."""
        resource = self.get_object()
        resource.is_active = False
        resource.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, permission_classes=[permissions.IsAuthenticated])
    def mine(self, request: Request) -> Response:
        resources = Resource.objects.filter(owner=request.user).select_related("owner")
        page = self.paginate_queryset(resources)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    @extend_schema(responses=WebhookSecretSerializer)
    @action(
        detail=True, url_path="webhook-secret", permission_classes=[permissions.IsAuthenticated]
    )
    def webhook_secret(self, request: Request, pk: str) -> Response:
        """The key that signs this resource's webhooks; receivers check X-Booking-Signature."""
        return Response({"secret": self.get_object().webhook_secret})

    @extend_schema(request=None, responses=WebhookSecretSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path="rotate-webhook-secret",
        permission_classes=[permissions.IsAuthenticated],
    )
    def rotate_webhook_secret(self, request: Request, pk: str) -> Response:
        resource = self.get_object()
        resource.webhook_secret = new_webhook_secret()
        resource.save(update_fields=["webhook_secret"])
        return Response({"secret": resource.webhook_secret})
