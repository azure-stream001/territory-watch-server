from django.db import models
from areas.models import Area


class DetectionJob(models.Model):
    """検出ジョブ（1回の変化検出実行）"""

    class Status(models.TextChoices):
        PENDING = "pending", "待機"
        RUNNING = "running", "実行中"
        COMPLETED = "completed", "完了"
        FAILED = "failed", "失敗"
        CANCELLED = "cancelled", "キャンセル"

    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name="detection_jobs")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # 検出パラメータ（仕様書の NDVI 閾値、最小面積など）
    params = models.JSONField("パラメータ", default=dict)
    # 期間
    before_date_start = models.DateField("Before 開始日", null=True, blank=True)
    before_date_end = models.DateField("Before 終了日", null=True, blank=True)
    after_date_start = models.DateField("After 開始日", null=True, blank=True)
    after_date_end = models.DateField("After 終了日", null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "検出ジョブ"
        verbose_name_plural = "検出ジョブ"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Job #{self.id} ({self.area.name}) - {self.status}"


class DetectionResult(models.Model):
    """検出結果（ジョブ1件につき1結果）"""

    job = models.OneToOneField(
        DetectionJob, on_delete=models.CASCADE, related_name="result"
    )
    # 比較に使用した Before/After シーン（結果画面で正しい地図・シーン詳細へリンクするため）
    before_scene = models.ForeignKey(
        "satellite.SatelliteScene",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    after_scene = models.ForeignKey(
        "satellite.SatelliteScene",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # 検出された伐採エリアの GeoJSON
    deforestation_geojson = models.JSONField("伐採エリア GeoJSON", null=True, blank=True)
    # 面積(ha)、パッチ数、分割申請疑いなど
    metrics = models.JSONField("メトリクス", default=dict)
    # 違法性判定結果（仕様書の IllegalityDetector 出力）
    violations = models.JSONField("違反疑い", default=list)
    # 精度評価（AccuracyAssessment の結果）
    accuracy = models.JSONField("精度指標", default=dict, blank=True)
    # 検出結果用の処理済み衛星画像（After シーン RGB）。MEDIA 相対パス。
    result_map_image = models.CharField(
        "結果地図画像", max_length=512, blank=True
    )
    # ImageOverlay 用 [south, west, north, east]
    result_map_bounds = models.JSONField("結果地図 bounds", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "検出結果"
        verbose_name_plural = "検出結果"

    def __str__(self):
        return f"Result for Job #{self.job_id}"
