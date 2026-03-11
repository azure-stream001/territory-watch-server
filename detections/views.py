from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import DetectionJob, DetectionResult
from .serializers import (
    DetectionJobSerializer,
    DetectionJobCreateSerializer,
    DetectionResultSerializer,
)


class DetectionJobViewSet(viewsets.ModelViewSet):
    queryset = DetectionJob.objects.select_related("area").all()
    serializer_class = DetectionJobSerializer
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "create":
            return DetectionJobCreateSerializer
        return DetectionJobSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.save()
        # 同期的にパイプラインを実行。失敗時は job を FAILED にして 201 で返す
        from .tasks import run_detection_job
        try:
            run_detection_job(job.id)
        except Exception:
            # タスク内で status/error_message は更新済み
            pass
        job.refresh_from_db()
        return Response(
            DetectionJobSerializer(job).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def result(self, request, pk=None):
        job = self.get_object()
        try:
            result = job.result
            return Response(DetectionResultSerializer(result).data)
        except DetectionResult.DoesNotExist:
            # 結果がない場合も 200 で job の状態を返し、frontend で status/error_message を表示できるようにする
            return Response(
                {
                    "result": None,
                    "job_status": job.status,
                    "job_error_message": job.error_message or "",
                },
                status=status.HTTP_200_OK,
            )


class DetectionResultViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DetectionResult.objects.select_related("job").all()
    serializer_class = DetectionResultSerializer
    permission_classes = [AllowAny]
    filterset_fields = ["job"]
    search_fields = []
