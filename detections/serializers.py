from django.conf import settings
from rest_framework import serializers
from areas.serializers import AreaSerializer
from satellite.serializers import SatelliteSceneSerializer
from .models import DetectionJob, DetectionResult


class DetectionJobSerializer(serializers.ModelSerializer):
    area_detail = AreaSerializer(source="area", read_only=True)

    class Meta:
        model = DetectionJob
        fields = [
            "id",
            "area",
            "area_detail",
            "status",
            "params",
            "before_date_start",
            "before_date_end",
            "after_date_start",
            "after_date_end",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "error_message"]


class DetectionResultSerializer(serializers.ModelSerializer):
    before_scene_detail = SatelliteSceneSerializer(source="before_scene", read_only=True)
    after_scene_detail = SatelliteSceneSerializer(source="after_scene", read_only=True)
    result_map_image_url = serializers.SerializerMethodField()

    class Meta:
        model = DetectionResult
        fields = [
            "id",
            "job",
            "before_scene",
            "after_scene",
            "before_scene_detail",
            "after_scene_detail",
            "deforestation_geojson",
            "metrics",
            "violations",
            "accuracy",
            "result_map_image_url",
            "result_map_bounds",
            "created_at",
        ]

    def get_result_map_image_url(self, obj):
        if not obj.result_map_image:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(settings.MEDIA_URL + obj.result_map_image)
        return (settings.MEDIA_URL or "/media/") + obj.result_map_image


class DetectionJobCreateSerializer(serializers.ModelSerializer):
    """作成時は Before（開発前）開始日 と After（開発後）終了日 のみ必須。窓 [before_start, after_end] で最近のシーンを使用。"""

    class Meta:
        model = DetectionJob
        fields = [
            "area",
            "params",
            "before_date_start",
            "before_date_end",
            "after_date_start",
            "after_date_end",
        ]

    def validate(self, attrs):
        """Before開始日 と After終了日 を必須にし、窓を [before_start, after_end] にそろえる。"""
        before_start = attrs.get("before_date_start")
        after_end = attrs.get("after_date_end")
        if not before_start or not after_end:
            raise serializers.ValidationError(
                "実データで検出するには、Before（開発前）開始日 と After（開発後）終了日 を指定してください。"
            )
        if after_end < before_start:
            raise serializers.ValidationError("After 終了日は Before 開始日以降にしてください。")
        # 未送信時は Before 終了日・After 開始日を重ならないよう前半/後半で埋める
        if "before_date_end" not in attrs or "after_date_start" not in attrs:
            total_days = (after_end - before_start).days
            half = max(0, total_days // 2)
            from datetime import timedelta
            mid = before_start + timedelta(days=half)
            attrs.setdefault("before_date_end", mid)
            attrs.setdefault("after_date_start", mid + timedelta(days=1))
        return attrs
