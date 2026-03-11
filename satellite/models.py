"""
衛星シーンの保存（地域・日付ごと）。比較用の Before/After を管理。
"""
from django.db import models


class SatelliteScene(models.Model):
    """対象地域の特定日の Sentinel-2 シーン。取得済みなら file_path に保存。"""

    class Status(models.TextChoices):
        PENDING = "pending", "取得待ち"
        DOWNLOADING = "downloading", "取得中"
        DOWNLOADED = "downloaded", "取得済み"
        FAILED = "failed", "取得失敗"

    area = models.ForeignKey(
        "areas.Area",
        on_delete=models.CASCADE,
        related_name="satellite_scenes",
    )
    scene_date = models.DateField("シーン日付")
    product_id = models.CharField("Sentinel product ID", max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    file_path = models.CharField(
        "ダウンロード済みファイルパス",
        max_length=512,
        blank=True,
        help_text="MEDIA_ROOT 相対または絶対パス",
    )
    metadata = models.JSONField("メタデータ", default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "衛星シーン"
        verbose_name_plural = "衛星シーン"
        ordering = ["-scene_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["area", "scene_date"],
                name="unique_area_scene_date",
            )
        ]

    def __str__(self):
        return f"{self.area.name} @ {self.scene_date} ({self.status})"
