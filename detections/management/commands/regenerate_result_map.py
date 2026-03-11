"""
Regenerate the result map image (RGB + bounds) for a detection result.
Use after improving result_map.py (e.g. contrast stretch) to refresh existing results.

  python manage.py regenerate_result_map 27
  python manage.py regenerate_result_map --result-id 27
"""
from django.core.management.base import BaseCommand

from detections.models import DetectionResult
from satellite.result_map import generate_result_map_image


class Command(BaseCommand):
    help = "Regenerate result map image (and bounds) for a detection result by ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "result_id",
            nargs="?",
            type=int,
            help="Detection result ID (e.g. 27)",
        )
        parser.add_argument(
            "--result-id",
            type=int,
            dest="result_id_opt",
            help="Alternative: --result-id 27",
        )

    def handle(self, *args, **options):
        result_id = options.get("result_id") or options.get("result_id_opt")
        if not result_id:
            self.stdout.write(self.style.ERROR("Provide result_id: python manage.py regenerate_result_map 27"))
            return
        try:
            result = DetectionResult.objects.get(pk=result_id)
        except DetectionResult.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Result {result_id} not found."))
            return
        rel_path, bounds = generate_result_map_image(result_id)
        if not rel_path:
            self.stdout.write(self.style.WARNING(f"Could not generate map for result {result_id} (missing after_scene or bands)."))
            return
        result.result_map_image = rel_path
        result.result_map_bounds = bounds
        result.save(update_fields=["result_map_image", "result_map_bounds"])
        self.stdout.write(self.style.SUCCESS(f"Regenerated result map for result {result_id}: {rel_path}"))
