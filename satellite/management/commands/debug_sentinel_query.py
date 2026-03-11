"""
Run the same Sentinel-2 query the app uses and print the real API response or error.
Usage: python manage.py debug_sentinel_query [area_id] [scene_date]
  area_id: default 1.  scene_date: default 2017-01-01 (YYYY-MM-DD).
"""
import datetime
from django.core.management.base import BaseCommand
from areas.models import Area
from satellite.sentinel_client import query_products
from satellite.dataspace_client import get_client_credentials, get_password_credentials, has_dataspace_credentials


class Command(BaseCommand):
    help = "Debug Sentinel-2 Copernicus query for an area/date (see why scene fetch fails)."

    def add_arguments(self, parser):
        parser.add_argument("area_id", nargs="?", type=int, default=1)
        parser.add_argument(
            "scene_date",
            nargs="?",
            type=str,
            default="2017-01-01",
            help="YYYY-MM-DD",
        )

    def handle(self, *args, **options):
        area_id = options["area_id"]
        scene_date_str = options["scene_date"]
        try:
            scene_date = datetime.datetime.strptime(scene_date_str, "%Y-%m-%d").date()
        except ValueError:
            self.stdout.write(self.style.ERROR(f"Invalid date: {scene_date_str}. Use YYYY-MM-DD."))
            return

        try:
            area = Area.objects.get(pk=area_id)
        except Area.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Area id={area_id} not found."))
            return

        footprint = (area.footprint_wkt or "").strip()
        if not footprint:
            lon = area.center_lon or 139.1147
            lat = area.center_lat or 34.9656
            d = 0.05
            footprint = (
                f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))"
            )

        date_start = scene_date - datetime.timedelta(days=30)
        date_end = scene_date + datetime.timedelta(days=30)
        date_start_str = date_start.strftime("%Y%m%d")
        date_end_str = date_end.strftime("%Y%m%d")

        self.stdout.write(f"Area: {area.name} (id={area.id})")
        self.stdout.write(f"Scene date: {scene_date}, search range: {date_start_str} - {date_end_str}")
        self.stdout.write(f"Footprint: {footprint[:80]}...")
        self.stdout.write("")

        if has_dataspace_credentials():
            if get_password_credentials():
                self.stdout.write("Using Copernicus Data Space (CDSE_USERNAME / CDSE_PASSWORD).")
            elif get_client_credentials():
                self.stdout.write("Using Copernicus Data Space (CDSE_CLIENT_ID / CDSE_CLIENT_SECRET).")
            else:
                self.stdout.write("Using Copernicus Data Space.")
        else:
            self.stdout.write("Using legacy SciHub (SENTINEL_USER / SENTINEL_PASSWORD) if set.")
        self.stdout.write("Querying (Sentinel-2 L2A, cloud ≤50%)...")

        try:
            items = query_products(
                footprint,
                date_start_str,
                date_end_str,
                cloud_cover=(0, 50),
            )
            self.stdout.write(self.style.SUCCESS(f"Products found: {len(items)}"))
            if items:
                pid, info = items[0]
                self.stdout.write(f"  First product Id: {pid}")
                if isinstance(info, dict):
                    self.stdout.write(f"  Name: {info.get('Name', '?')}")
                    self.stdout.write(f"  Cloud cover: {info.get('cloudCover', info.get('cloudcoverpercentage', '?'))}")
            else:
                self.stdout.write(
                    "  No L2A scenes in this area/date/cloud range. Try another date (e.g. 2017-06-15) or relax cloud."
                )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"API error: {type(e).__name__}: {e}"))
            self.stdout.write("")
            if has_dataspace_credentials():
                self.stdout.write("Check credentials in .env (CDSE_USERNAME/PASSWORD or CDSE_CLIENT_ID/SECRET).")
            else:
                self.stdout.write("Set CDSE_USERNAME and CDSE_PASSWORD, or CDSE_CLIENT_ID and CDSE_CLIENT_SECRET, in .env.")
