from django.db import models

from .geo_utils import wkt_polygon_area_ha


class Area(models.Model):
    """対象地域（例: 伊東市八幡野地区）"""

    name = models.CharField("地域名", max_length=255)
    description = models.TextField("説明", blank=True)
    # Center for map display (no PostGIS in minimal setup)
    center_lat = models.FloatField("中心緯度", null=True, blank=True)
    center_lon = models.FloatField("中心経度", null=True, blank=True)
    # Footprint as WKT or store in JSON for simplicity
    footprint_wkt = models.TextField(
        "範囲（WKT POLYGON）",
        blank=True,
        help_text="e.g. POLYGON((139.10 34.94, 139.13 34.94, ...))",
    )
    metadata = models.JSONField("メタデータ", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "対象地域"
        verbose_name_plural = "対象地域"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """保存時に footprint_wkt から面積を算出し metadata['area_ha'] に格納する。"""
        if self.footprint_wkt:
            area_ha = wkt_polygon_area_ha(self.footprint_wkt)
            if area_ha is not None:
                if self.metadata is None:
                    self.metadata = {}
                self.metadata = dict(self.metadata)
                self.metadata["area_ha"] = area_ha
        super().save(*args, **kwargs)
