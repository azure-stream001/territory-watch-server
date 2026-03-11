"""
WKT ポリゴンから面積（ヘクタール）を算出するユーティリティ。
測地系（WGS84）で面積を計算し、ha で返す。
"""

from shapely import wkt
from shapely.ops import orient


def wkt_polygon_area_ha(wkt_string: str) -> float | None:
    """
    WKT 文字列（POLYGON または MULTIPOLYGON）から測地面積を計算し、ヘクタールで返す。
    パース失敗・空の場合は None。
    """
    if not (wkt_string or "").strip():
        return None
    try:
        geom = wkt.loads(wkt_string.strip())
    except Exception:
        return None
    if geom is None or geom.is_empty:
        return None

    try:
        from pyproj import Geod
    except ImportError:
        return None

    geod = Geod(ellps="WGS84")

    if geom.geom_type == "Polygon":
        # 外リングのみで面積計算（反時計回りに統一）
        oriented = orient(geom, sign=1.0)
        area_m2, _ = geod.geometry_area_perimeter(oriented)
        return round(abs(area_m2) / 10000.0, 4)
    if geom.geom_type == "MultiPolygon":
        total_m2 = 0.0
        for poly in geom.geoms:
            oriented = orient(poly, sign=1.0)
            area_m2, _ = geod.geometry_area_perimeter(oriented)
            total_m2 += abs(area_m2)
        return round(total_m2 / 10000.0, 4)
    return None
