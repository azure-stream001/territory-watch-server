from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SatelliteSceneViewSet, ndvi_change_view

router = DefaultRouter()
router.register(r"scenes", SatelliteSceneViewSet, basename="scene")

urlpatterns = [
    path("ndvi-change/", ndvi_change_view),
    path("", include(router.urls)),
]
