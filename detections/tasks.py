"""
Celery tasks for detection jobs（仕様書 Phase 2）.
run_pipeline で変化検出・違法性判定を実行し結果を保存。
"""
from celery import shared_task

from .models import DetectionJob, DetectionResult


@shared_task(bind=True)
def run_detection_job(self, job_id: int):
    """
    検出ジョブを実行: 前処理 → 変化検出 → 違法性判定 → 精度評価 → 結果保存。
    """
    try:
        job = DetectionJob.objects.select_related("area").get(pk=job_id)
    except DetectionJob.DoesNotExist:
        return {"error": "Job not found"}

    job.status = DetectionJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])

    try:
        from satellite.pipeline import run_pipeline

        out = run_pipeline(job)
        if len(out) == 2 and out[0] == "saved_for_next_time":
            job.status = DetectionJob.Status.COMPLETED
            job.error_message = out[1] or "Scene saved for next comparison."
            job.save(update_fields=["status", "error_message", "updated_at"])
            return {"job_id": job_id, "saved_for_next_time": True, "message": out[1]}
        deforestation_geojson, metrics, violations, accuracy, before_scene_id, after_scene_id = out
        result = DetectionResult.objects.create(
            job=job,
            before_scene_id=before_scene_id,
            after_scene_id=after_scene_id,
            deforestation_geojson=deforestation_geojson,
            metrics=metrics,
            violations=violations,
            accuracy=accuracy,
        )
        try:
            from satellite.result_map import generate_result_map_image
            rel_path, bounds = generate_result_map_image(result.id)
            if rel_path:
                result.result_map_image = rel_path
                result.result_map_bounds = bounds
                result.save(update_fields=["result_map_image", "result_map_bounds"])
        except Exception:
            pass
        job.status = DetectionJob.Status.COMPLETED
        job.error_message = ""
        job.save(update_fields=["status", "error_message", "updated_at"])
        return {"job_id": job_id, "result_id": result.id}
    except Exception as e:
        job.status = DetectionJob.Status.FAILED
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message", "updated_at"])
        raise
