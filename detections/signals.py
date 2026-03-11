"""
Clean up detection result files when a DetectionResult (or its job) is deleted.
Removes the detection_results/{id}/ directory (map.png, etc.).
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import DetectionResult


def _result_dir_for_result(result_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / "detection_results" / str(result_id)


@receiver(post_delete, sender=DetectionResult)
def delete_detection_result_files(sender, instance: DetectionResult, **kwargs):
    """Remove detection_results/{id}/ when a result is deleted (e.g. job removed)."""
    result_dir = _result_dir_for_result(instance.id)
    if result_dir.exists():
        try:
            shutil.rmtree(result_dir)
        except OSError:
            pass
