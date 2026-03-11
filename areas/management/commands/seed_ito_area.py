"""
伊東市八幡野地区を Area として1件登録する（仕様書の検証対象地域）.
"""
from django.core.management.base import BaseCommand
from areas.models import Area


# 仕様書より: 座標 34.9656° N, 139.1147° E / 約105ha
ITO_FOOTPRINT = (
    "POLYGON((139.10 34.94, 139.13 34.94, 139.13 34.98, 139.10 34.98, 139.10 34.94))"
)


class Command(BaseCommand):
    help = "Create Ito City Yawata-no area (prototype target)."

    def handle(self, *args, **options):
        area, created = Area.objects.update_or_create(
            name="伊東市八幡野地区",
            defaults={
                "description": "2018年メガソーラー開発による大規模森林伐採の検証対象地域。環境アセス回避のため複数事業者で分割申請。",
                "center_lat": 34.9656,
                "center_lon": 139.1147,
                "footprint_wkt": ITO_FOOTPRINT,
                "metadata": {
                    "prefecture": "静岡県",
                    "area_ha": 105,
                    "reference_year": 2018,
                },
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created area: {area.name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated area: {area.name}"))
