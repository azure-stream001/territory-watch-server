from django.contrib import admin
from .models import SatelliteScene


@admin.register(SatelliteScene)
class SatelliteSceneAdmin(admin.ModelAdmin):
    list_display = ["id", "area", "scene_date", "status", "product_id", "created_at"]
    list_filter = ["status", "area"]
    search_fields = ["product_id", "area__name"]
    raw_id_fields = ["area"]
