from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import generics, mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from bookings import services
from bookings.idempotency import KEY_PATTERN, KeyReused, request_fingerprint, run_once
from bookings.models import Booking
from bookings.periods import PeriodError
from bookings.serializers import (
    AvailabilityQuerySerializer,
    AvailabilitySerializer,
    BookingCreateSerializer,
    BookingRangeQuerySerializer,
    BookingSerializer,
)
from resources.models import Resource

IDEMPOTENCY_HEADER = OpenApiParameter(
    "Idempotency-Key",
    OpenApiTypes.STR,
    OpenApiParameter.HEADER,
    description="8 to 100 letters, digits or _.:- ; a repeated request returns the first answer",
)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class KeyReusedError(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "this Idempotency-Key was already used with a different request"
    default_code = "idempotency_key_reused"


class BookingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Customers hold a slot, then confirm it; customers and resource owners can cancel."""

    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "bookings"
    lookup_value_regex = "[0-9]+"

    def get_throttles(self):
        if self.action == "create":
            return [*super().get_throttles(), ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        user = self.request.user
        bookings = Booking.objects.select_related("resource", "customer").order_by("-id")
        if self.action == "list":
            return bookings.filter(customer=user)  # owners use /resources/{id}/bookings/
        return bookings.filter(Q(customer=user) | Q(resource__owner=user))

    @extend_schema(
        request=BookingCreateSerializer,
        parameters=[IDEMPOTENCY_HEADER],
        responses={201: BookingSerializer},
    )
    def create(self, request: Request) -> Response:
        key = request.headers.get("Idempotency-Key")
        if key is None:
            code, body = self.hold(request)
            return Response(body, status=code)
        if not KEY_PATTERN.match(key):
            raise serializers.ValidationError(
                {"Idempotency-Key": ["8 to 100 letters, digits or _.:-"]}
            )
        try:
            code, body, replayed = run_once(
                user=request.user,
                key=key,
                fingerprint=request_fingerprint(request.data),
                handler=lambda: self.hold(request),
            )
        except KeyReused as error:
            raise KeyReusedError from error
        response = Response(body, status=code)
        if replayed:
            response["Idempotent-Replayed"] = "true"
        return response

    def hold(self, request: Request) -> tuple[int, dict]:
        data = BookingCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            booking = services.hold(customer=request.user, **data.validated_data)
        except PeriodError as error:
            raise serializers.ValidationError({"start": [str(error)]}) from error
        except services.SlotTaken as error:
            raise Conflict(str(error)) from error
        return status.HTTP_201_CREATED, dict(BookingSerializer(booking).data)

    @extend_schema(request=None, responses=BookingSerializer)
    @action(detail=True, methods=["post"])
    def confirm(self, request: Request, pk: str) -> Response:
        booking = self.change(services.confirm, pk, customer=request.user)
        return Response(BookingSerializer(booking).data)

    @extend_schema(request=None, responses=BookingSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: str) -> Response:
        booking = self.change(services.cancel, pk, user=request.user)
        return Response(BookingSerializer(booking).data)

    def change(self, operation, pk: str, **who) -> Booking:
        try:
            return operation(int(pk), **who)
        except (Booking.DoesNotExist, ValueError) as error:
            raise NotFound("no such booking") from error
        except services.BookingError as error:
            raise Conflict(str(error)) from error


class ResourceAvailabilityView(APIView):
    """Free stretches of one day that are long enough to book, in the resource's zone."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(parameters=[AvailabilityQuerySerializer], responses=AvailabilitySerializer)
    def get(self, request: Request, pk: int) -> Response:
        resource = get_object_or_404(Resource, pk=pk, is_active=True)
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        day = query.validated_data["date"]
        free = [
            {"start": start, "end": end}
            for start, end in services.availability(resource, day, now=timezone.now())
        ]
        body = {"resource": resource.pk, "date": day, "timezone": resource.timezone, "free": free}
        with timezone.override(resource.zone):  # DRF renders datetimes in the active zone
            return Response(AvailabilitySerializer(body).data)


@extend_schema(parameters=[BookingRangeQuerySerializer])
class ResourceBookingsView(generics.ListAPIView):
    """Every booking of the owner's resource, by start time."""

    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation has no URL kwargs
            return Booking.objects.none()
        resource = get_object_or_404(Resource, pk=self.kwargs["pk"], owner=self.request.user)
        query = BookingRangeQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        bookings = resource.bookings.select_related("resource", "customer").order_by("period")
        if start_from := query.validated_data.get("start_from"):
            bookings = bookings.filter(period__startswith__gte=start_from)
        if start_to := query.validated_data.get("start_to"):
            bookings = bookings.filter(period__startswith__lt=start_to)
        return bookings
