"""
伊東市八幡野地区で検出ジョブを1件作成し、同期的に実行（仕様書 Phase 2 検証用）。
"""
from django.core.management.base import BaseCommand
from areas.models import Area
from detections.models import DetectionJob, DetectionResult
from detections.tasks import run_detection_job


class Command(BaseCommand):
    help = "Create and run one detection job for Ito City Yawata-no area (spec Phase 2)."

    def handle(self, *args, **options):
        area = Area.objects.filter(name__icontains="伊東").first()
        if not area:
            self.stdout.write(self.style.ERROR("No area found. Run: python manage.py seed_ito_area"))
            return

        job = DetectionJob.objects.create(
            area=area,
            status=DetectionJob.Status.PENDING,
            params={"ndvi_threshold": -0.3, "min_area_ha": 1.0},
            before_date_start="2017-01-01",
            before_date_end="2017-12-31",
            after_date_start="2019-01-01",
            after_date_end="2019-12-31",
        )
        self.stdout.write(f"Created job id={job.id}, running pipeline...")
        run_detection_job(job.id)
        job.refresh_from_db()
        if job.status == DetectionJob.Status.COMPLETED:
            r = job.result
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. detected_area_ha={r.metrics.get('detected_area_ha')}, "
                    f"violations={len(r.violations)}"
                )
            )
        else:
            self.stdout.write(self.style.ERROR(f"Failed: {job.error_message}"))
