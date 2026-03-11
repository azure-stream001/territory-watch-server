"""
ラベル付きラスタから GeoJSON 生成.
ピクセル座標を中心緯度経度ベースの簡易座標に変換。
検出結果を対象地域ポリゴン内にクリップするユーティリティ。
"""
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def area_bbox_wgs84(
    area: Any,
    margin_fraction: float = 0.0,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Area の footprint または中心から (west, south, east, north) WGS84 の bbox を返す。
    ポリゴンを含む最小の表示範囲（tight bbox）にする。margin_fraction で周囲を拡大可能（0=ぴったり）。
    """
    wkt = (getattr(area, "footprint_wkt", None) or "").strip()
    if wkt:
        coords = re.findall(r"[-\d.]+\s+[-\d.]+", wkt)
        if coords:
            pairs = [tuple(map(float, c.split())) for c in coords]
            a0 = [p[0] for p in pairs]
            a1 = [p[1] for p in pairs]
            # WKT is usually (lon lat). If values look like (lat, lon) for Japan (lon 128–146, lat 30–46), swap.
            if all(24 <= v <= 52 for v in a0) and all(122 <= v <= 154 for v in a1):
                a0, a1 = a1, a0
            west, south, east, north = min(a0), min(a1), max(a0), max(a1)
            if margin_fraction > 0 and (east > west and north > south):
                w = east - west
                h = north - south
                west -= w * margin_fraction
                east += w * margin_fraction
                south -= h * margin_fraction
                north += h * margin_fraction
            return (west, south, east, north)
    lon = getattr(area, "center_lon", None)
    lat = getattr(area, "center_lat", None)
    if lon is not None and lat is not None:
        # フォールバック時は小さめの範囲（約1km）で「適切なサイズ」にする
        d = 0.01
        return (lon - d, lat - d, lon + d, lat + d)
    return None


def polygon_center_and_crop_bbox(
    area: Any,
    display_padding: float = 1.5,
    load_padding: float = 3.0,
) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[Tuple[float, float, float, float]]]:
    """
    ポリゴンの重心（centroid）と対角線半径を計算し、重心を中心とした
    表示用・ロード用の正方形 bbox を返す。

    手順:
      1. 頂点座標の平均で重心を求める
      2. 各頂点から重心までの距離の最大値を「半径」とする
      3. 表示用 bbox  = 重心 ± radius × display_padding （ポリゴンが確実に収まる表示範囲）
      4. ロード用 bbox = 重心 ± radius × load_padding  （クロップ後のラスタがポリゴンをカバーする）

    Returns: (display_bbox, load_bbox) ともに (west, south, east, north) WGS84。
             失敗時は (None, None)。
    """
    center_lon: Optional[float] = None
    center_lat: Optional[float] = None
    radius_m: float = 5000.0  # fallback: 5 km

    wkt = (getattr(area, "footprint_wkt", None) or "").strip()
    if wkt:
        coords = re.findall(r"[-\d.]+\s+[-\d.]+", wkt)
        if coords:
            pairs = [tuple(map(float, c.split())) for c in coords]
            a0 = [p[0] for p in pairs]
            a1 = [p[1] for p in pairs]
            # WKT は通常 (lon lat)。日本座標で (lat, lon) の場合は入れ替え
            if all(24 <= v <= 52 for v in a0) and all(122 <= v <= 154 for v in a1):
                a0, a1 = a1, a0
            lons, lats = a0, a1
            # 重心 = 頂点座標の平均（クローズドリングなので最後の点が最初と同じ場合を除く）
            pts = list(zip(lons, lats))
            if pts and pts[0] == pts[-1]:
                pts = pts[:-1]
            if pts:
                center_lon = sum(p[0] for p in pts) / len(pts)
                center_lat = sum(p[1] for p in pts) / len(pts)
                # 各頂点から重心までの距離の最大値（メートル）
                m_per_deg_lon = 111320.0 * math.cos(math.radians(center_lat))
                m_per_deg_lat = 110540.0
                max_r = 0.0
                for lon, lat in pts:
                    dx = (lon - center_lon) * m_per_deg_lon
                    dy = (lat - center_lat) * m_per_deg_lat
                    max_r = max(max_r, math.sqrt(dx * dx + dy * dy))
                radius_m = max(max_r, 500.0)  # 最小 500m

    if center_lon is None:
        center_lon = getattr(area, "center_lon", None)
        center_lat = getattr(area, "center_lat", None)
        if center_lon is None or center_lat is None:
            return None, None

    m_per_deg_lon = 111320.0 * math.cos(math.radians(center_lat))
    m_per_deg_lat = 110540.0

    def _square_bbox(padding: float) -> Tuple[float, float, float, float]:
        half_m = radius_m * padding
        d_lon = half_m / m_per_deg_lon
        d_lat = half_m / m_per_deg_lat
        return (
            center_lon - d_lon,  # west
            center_lat - d_lat,  # south
            center_lon + d_lon,  # east
            center_lat + d_lat,  # north
        )

    return _square_bbox(display_padding), _square_bbox(load_padding)


def raster_bounds_from_center(
    rows: int,
    cols: int,
    center_lon: float,
    center_lat: float,
    pixel_size_m: float = 10.0,
) -> Tuple[float, float, float, float]:
    """
    ラスタの行数・列数と中心・ピクセルサイズから地理範囲を返す。
    Returns (min_lon, min_lat, max_lon, max_lat).
    """
    deg_per_m_lon = 1.0 / (111320 * max(1e-6, np.cos(np.radians(center_lat))))
    deg_per_m_lat = 1.0 / 110540
    half_w_m = (cols / 2) * pixel_size_m
    half_h_m = (rows / 2) * pixel_size_m
    min_lon = center_lon - half_w_m * deg_per_m_lon
    max_lon = center_lon + half_w_m * deg_per_m_lon
    min_lat = center_lat - half_h_m * deg_per_m_lat
    max_lat = center_lat + half_h_m * deg_per_m_lat
    return (min_lon, min_lat, max_lon, max_lat)


def pixel_to_geo(
    px: float, py: float,
    rows: int, cols: int,
    center_lon: float, center_lat: float,
    pixel_size_m: float = 10.0,
) -> tuple:
    """
    ピクセル (col, row) を経緯度に変換。
    中心が (center_lon, center_lat)、1ピクセル = pixel_size_m メートルと仮定。
    """
    deg_per_m_lon = 1.0 / (111320 * np.cos(np.radians(center_lat)))
    deg_per_m_lat = 1.0 / 110540
    dx_m = (px - cols / 2) * pixel_size_m
    dy_m = (py - rows / 2) * pixel_size_m
    lon = center_lon + dx_m * deg_per_m_lon
    lat = center_lat - dy_m * deg_per_m_lat
    return (lon, lat)


def labeled_to_geojson(
    labeled: np.ndarray,
    center_lon: float,
    center_lat: float,
    pixel_size_m: float = 10.0,
) -> Dict[str, Any]:
    """
    ラベル付きラスタから GeoJSON FeatureCollection を生成。
    各パッチは Point ジオメトリと area_ha プロパティ。
    """
    rows, cols = labeled.shape
    features: List[Dict[str, Any]] = []
    for uid in np.unique(labeled):
        if uid == 0:
            continue
        mask = labeled == uid
        ys, xs = np.where(mask)
        cx, cy = float(np.mean(xs)), float(np.mean(ys))
        lon, lat = pixel_to_geo(cx, cy, rows, cols, center_lon, center_lat, pixel_size_m)
        area_pixels = int(np.sum(mask))
        area_ha = area_pixels * (pixel_size_m ** 2) / 10000.0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"patch_id": int(uid), "area_ha": round(area_ha, 4)},
        })
    return {"type": "FeatureCollection", "features": features}


def _transform_ring_pixel_to_geo(
    ring: List[Tuple[float, float]],
    rows: int,
    cols: int,
    center_lon: float,
    center_lat: float,
    pixel_size_m: float,
) -> List[List[float]]:
    """Ring of (col, row) pixel coords to GeoJSON linear ring [lon, lat], closed."""
    out = []
    for px, py in ring:
        lon, lat = pixel_to_geo(px, py, rows, cols, center_lon, center_lat, pixel_size_m)
        out.append([lon, lat])
    if out and (out[0][0] != out[-1][0] or out[0][1] != out[-1][1]):
        out.append(out[0][:])
    return out


def labeled_to_geojson_polygons(
    labeled: np.ndarray,
    center_lon: float,
    center_lat: float,
    pixel_size_m: float = 10.0,
) -> Dict[str, Any]:
    """
    ラベル付きラスタから GeoJSON FeatureCollection を生成（Polygon ジオメトリ）。
    各パッチは輪郭ポリゴンと area_ha プロパティ。地図上で赤枠表示用。
    """
    try:
        import rasterio.features
    except ImportError:
        return labeled_to_geojson(labeled, center_lon, center_lat, pixel_size_m)

    rows, cols = labeled.shape
    features: List[Dict[str, Any]] = []
    for uid in np.unique(labeled):
        if uid == 0:
            continue
        mask = (labeled == uid).astype(np.uint8)
        area_pixels = int(np.sum(mask))
        area_ha = area_pixels * (pixel_size_m ** 2) / 10000.0
        for geom, _ in rasterio.features.shapes(mask, transform=rasterio.Affine.identity(), mask=mask):
            if geom["type"] != "Polygon":
                continue
            coords = geom["coordinates"][0]
            ring = [(c[0], c[1]) for c in coords]
            geo_ring = _transform_ring_pixel_to_geo(
                ring, rows, cols, center_lon, center_lat, pixel_size_m
            )
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [geo_ring]},
                "properties": {"patch_id": int(uid), "area_ha": round(area_ha, 4)},
            })
            break
    return {"type": "FeatureCollection", "features": features}


def filter_geojson_to_area(
    geojson: Dict[str, Any],
    footprint_wkt: Optional[str],
) -> Dict[str, Any]:
    """
    GeoJSON FeatureCollection のうち、指定された地域ポリゴン（WKT）と
    交差する Feature だけを返す。検出エリア外の赤表示を防ぐため。
    footprint_wkt が空の場合はフィルタせずそのまま返す。
    """
    if not (footprint_wkt or "").strip():
        return geojson
    features = geojson.get("features") or []
    if not features:
        return geojson
    try:
        from shapely import wkt as wkt_loads
        from shapely.geometry import shape
        area_geom = wkt_loads.loads(footprint_wkt.strip())
        if area_geom is None or area_geom.is_empty:
            return geojson
    except Exception:
        return geojson
    filtered: List[Dict[str, Any]] = []
    for f in features:
        geom_dict = f.get("geometry")
        if not geom_dict:
            continue
        try:
            feat_geom = shape(geom_dict)
            if feat_geom.is_empty:
                continue
            if area_geom.intersects(feat_geom):
                filtered.append(f)
        except Exception:
            continue
    return {"type": "FeatureCollection", "features": filtered}
