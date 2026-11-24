from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from bookings.views import BookingViewSet, ResourceAvailabilityView, ResourceBookingsView
from core.views import HealthView
from resources.views import ResourceViewSet

router = DefaultRouter()
router.register("resources", ResourceViewSet, basename="resource")
router.register("bookings", BookingViewSet, basename="booking")

api = [
    path("", include(router.urls)),
    path(
        "resources/<int:pk>/availability/",
        ResourceAvailabilityView.as_view(),
        name="resource-availability",
    ),
    path("resources/<int:pk>/bookings/", ResourceBookingsView.as_view(), name="resource-bookings"),
    path("auth/", include("accounts.urls")),
    path("health/", HealthView.as_view(), name="health"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(api)),
]
