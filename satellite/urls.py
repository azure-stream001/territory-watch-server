from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SatelliteSceneViewSet, QuickDetectView, ndvi_change_view

router = DefaultRouter()
router.register(r"scenes", SatelliteSceneViewSet, basename="scene")

urlpatterns = [
    path("quick-detect/", QuickDetectView.as_view(), name="quick-detect"),
    path("ndvi-change/", ndvi_change_view),
    path("", include(router.urls)),
]
