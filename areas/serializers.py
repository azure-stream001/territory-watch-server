from rest_framework import serializers
from .models import Area


class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = [
            "id",
            "name",
            "description",
            "center_lat",
            "center_lon",
            "footprint_wkt",
            "metadata",
            "created_at",
            "updated_at",
        ]
