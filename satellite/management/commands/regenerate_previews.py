"""
Regenerate scene previews from Sentinel zip files.
Use after fixing bounds CRS (UTM → WGS84) to clear invalid cached previews.

Usage:
  python manage.py regenerate_previews           # all downloaded scenes
  python manage.py regenerate_previews --scene-id 32
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from satellite.models import SatelliteScene


def is_valid_wgs84_bounds(b: list) -> bool:
    """Check if bounds are valid WGS84 [south, west, north, east]."""
    if not b or len(b) != 4:
        return False
    return (
        -90 <= b[0] <= 90
        and -90 <= b[2] <= 90
        and -180 <= b[1] <= 180
        and -180 <= b[3] <= 180
    )


class Command(BaseCommand):
    help = "Regenerate scene previews (clears cache so next visit rebuilds with correct WGS84 bounds)."

    def add_arguments(self, parser):
        parser.add_argument("--scene-id", type=int, help="Only regenerate this scene")

    def handle(self, *args, **options):
        scene_id = options.get("scene_id")
        media_root = Path(settings.MEDIA_ROOT)
        previews_dir = media_root / "scene_previews"
        if not previews_dir.exists():
            self.stdout.write("No scene_previews directory.")
            return

        if scene_id:
            scene_dirs = [previews_dir / str(scene_id)] if (previews_dir / str(scene_id)).exists() else []
        else:
            scene_dirs = [d for d in previews_dir.iterdir() if d.is_dir()]

        import json
        cleared = 0
        for d in scene_dirs:
            bounds_path = d / "bounds.json"
            if not bounds_path.exists():
                continue
            try:
                with open(bounds_path) as f:
                    data = json.load(f)
                b = data.get("bounds")
                if is_valid_wgs84_bounds(b):
                    self.stdout.write(f"  {d.name}: valid WGS84, skip")
                    continue
            except (json.JSONDecodeError, OSError):
                pass
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            cleared += 1
            self.stdout.write(self.style.SUCCESS(f"  {d.name}: cleared (invalid bounds)"))

        self.stdout.write(f"\nCleared {cleared} preview(s). Visit scene pages to regenerate.")
