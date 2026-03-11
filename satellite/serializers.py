from rest_framework import serializers
from areas.models import Area
from areas.serializers import AreaSerializer
from .models import SatelliteScene


class SatelliteSceneSerializer(serializers.ModelSerializer):
    area_name = serializers.CharField(source="area.name", read_only=True)
    area_detail = AreaSerializer(source="area", read_only=True)

    class Meta:
        model = SatelliteScene
        fields = [
            "id",
            "area",
            "area_name",
            "area_detail",
            "scene_date",
            "product_id",
            "status",
            "file_path",
            "metadata",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "product_id",
            "status",
            "file_path",
            "metadata",
            "error_message",
            "created_at",
            "updated_at",
        ]


class SatelliteSceneCreateSerializer(serializers.Serializer):
    """シーン取得リクエスト: area + 日付 + 雲量上限（指定日以前で最も近い日・雲量以下のシーンを取得）"""
    area = serializers.PrimaryKeyRelatedField(queryset=Area.objects.all())
    scene_date = serializers.DateField(required=True)
    max_cloud_coverage = serializers.IntegerField(
        required=False,
        default=50,
        min_value=0,
        max_value=100,
        help_text="Maximum cloud cover (%). Only scenes with cloud cover at or below this value are considered. Nearest date to scene_date satisfying this is fetched.",
    )
