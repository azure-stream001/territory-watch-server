from django.contrib import admin
from .models import DetectionJob, DetectionResult


@admin.register(DetectionJob)
class DetectionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "area", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("area",)


@admin.register(DetectionResult)
class DetectionResultAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "created_at")
    raw_id_fields = ("job",)
