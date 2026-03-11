"""
Attach an existing Sentinel-2 zip (e.g. already downloaded) to a scene so the app can use it for preview and detection.

Usage:
  python manage.py use_sentinel_zip sentinel/S2B_MSIL2A_20260215T012649_N0512_R074_T54SUD_20260215T060004.SAFE.zip
  python manage.py use_sentinel_zip sentinel/S2B_MSIL2A_....zip --scene-id 1
  python manage.py use_sentinel_zip sentinel/S2B_MSIL2A_....zip --area-id 1 --scene-date 2026-02-15

Path is relative to MEDIA_ROOT. If neither --scene-id nor (--area-id + --scene-date) is given, the command tries to parse the date from the filename (YYYYMMDD in product name) and uses the first area.
"""
import datetime
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from areas.models import Area
from satellite.models import SatelliteScene


def parse_date_from_zip_name(path: str) -> datetime.date | None:
    """Extract YYYYMMDD from Sentinel-2 zip filename like ..._20260215T..._....SAFE.zip."""
    name = Path(path).stem
    match = re.search(r"_(\d{4})(\d{2})(\d{2})T", name)
    if match:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return datetime.date(y, m, d)
        except ValueError:
            pass
    return None


class Command(BaseCommand):
    help = "Link an existing Sentinel-2 zip to a scene (status=downloaded) so preview and detection can use it."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            type=str,
            help="Path to the zip relative to MEDIA_ROOT (e.g. sentinel/S2B_MSIL2A_....SAFE.zip)",
        )
        parser.add_argument("--scene-id", type=int, default=None, help="Update this scene by id.")
        parser.add_argument("--area-id", type=int, default=None, help="Use this area (with --scene-date).")
        parser.add_argument(
            "--scene-date",
            type=str,
            default=None,
            help="Scene date YYYY-MM-DD (required if --area-id is set).",
        )

    def handle(self, *args, **options):
        path = options["path"].strip()
        scene_id = options["scene_id"]
        area_id = options["area_id"]
        scene_date_str = options["scene_date"]

        abs_path = Path(settings.MEDIA_ROOT) / path
        if not abs_path.exists():
            self.stdout.write(self.style.ERROR(f"File not found: {abs_path}"))
            return

        if not path.lower().endswith(".zip"):
            self.stdout.write(self.style.WARNING("Path does not end with .zip; scene preview may expect a zip."))

        scene = None
        if scene_id is not None:
            try:
                scene = SatelliteScene.objects.select_related("area").get(pk=scene_id)
            except SatelliteScene.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Scene id={scene_id} not found."))
                return
        elif area_id is not None and scene_date_str:
            try:
                scene_date = datetime.datetime.strptime(scene_date_str, "%Y-%m-%d").date()
            except ValueError:
                self.stdout.write(self.style.ERROR(f"Invalid --scene-date: {scene_date_str}. Use YYYY-MM-DD."))
                return
            try:
                area = Area.objects.get(pk=area_id)
            except Area.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Area id={area_id} not found."))
                return
            scene, _ = SatelliteScene.objects.get_or_create(
                area=area,
                scene_date=scene_date,
                defaults={
                    "status": SatelliteScene.Status.PENDING,
                    "product_id": abs_path.stem[:255] if abs_path.stem else "",
                },
            )
        else:
            scene_date = parse_date_from_zip_name(path)
            if not scene_date:
                self.stdout.write(
                    self.style.ERROR("Could not parse date from filename. Use --scene-id or --area-id and --scene-date.")
                )
                return
            area = Area.objects.first()
            if not area:
                self.stdout.write(self.style.ERROR("No area in database. Create an area first."))
                return
            scene, created = SatelliteScene.objects.get_or_create(
                area=area,
                scene_date=scene_date,
                defaults={
                    "status": SatelliteScene.Status.PENDING,
                    "product_id": abs_path.stem[:255] if abs_path.stem else "",
                },
            )
            if created:
                self.stdout.write(f"Created scene id={scene.id} for area '{area.name}' and date {scene_date}.")

        scene.file_path = path
        scene.status = SatelliteScene.Status.DOWNLOADED
        if not scene.product_id and abs_path.stem:
            scene.product_id = abs_path.stem[:255]
        scene.error_message = ""
        scene.save(update_fields=["file_path", "status", "product_id", "error_message", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Scene id={scene.id} (area={scene.area.name}, date={scene.scene_date}) is now linked to {path}."
            )
        )
        self.stdout.write("Regenerate preview if needed: delete media/scene_previews/{}/ then open the scene detail page.".format(scene.id))
