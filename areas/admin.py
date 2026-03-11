from django.contrib import admin
from .models import Area


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("name", "center_lat", "center_lon", "created_at")
    search_fields = ("name",)
