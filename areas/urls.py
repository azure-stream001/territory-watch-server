from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AreaViewSet, GeoshapeTileProxyView

router = DefaultRouter()
router.register(r"areas", AreaViewSet, basename="area")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "geoshape-tile/<int:z>/<int:x>/<int:y>.pbf",
        GeoshapeTileProxyView.as_view(),
        name="geoshape-tile",
    ),
]
