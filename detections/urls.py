from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DetectionJobViewSet, DetectionResultViewSet

router = DefaultRouter()
router.register(r"detection-jobs", DetectionJobViewSet, basename="detection-job")
router.register(r"detection-results", DetectionResultViewSet, basename="detection-result")

urlpatterns = [
    path("", include(router.urls)),
]
