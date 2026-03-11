"""
Generate a processed satellite (RGB) map image for a detection result.
Uses the same centroid+radius logic as scene_preview so before/after/result maps
all show the polygon centered at the correct scale.
"""
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from django.conf import settings

from .geo_utils import area_bbox_wgs84, polygon_center_and_crop_bbox, raster_bounds_from_center
from .io_sentinel import load_bands_from_sentinel_product


def generate_result_map_image(result_id: int) -> Tuple[Optional[str], Optional[list]]:
    """
    Load the after-scene for the given result, create an RGB PNG, save to media.

    scene_preview と同じ centroid+radius アルゴリズムを使い、
    before/after/result の3つのマップが常に同じ範囲・中心で表示されるようにする。
    Returns (relative_path, bounds) or (None, None) on failure.
    bounds is [south, west, north, east] for Leaflet ImageOverlay.
    """
    from detections.models import DetectionResult

    try:
        result = DetectionResult.objects.select_related("job__area", "after_scene").get(pk=result_id)
    except DetectionResult.DoesNotExist:
        return None, None
    if not result.after_scene or not result.after_scene.file_path:
        return None, None

    area = result.job.area

    # scene_preview と同じ bbox 計算
    display_bbox, load_bbox = polygon_center_and_crop_bbox(
        area, display_padding=1.5, load_padding=3.0
    )
    if display_bbox is None:
        display_bbox = area_bbox_wgs84(area, margin_fraction=0.10)
        load_bbox = area_bbox_wgs84(area, margin_fraction=0.50)

    try:
        bands = load_bands_from_sentinel_product(
            result.after_scene.file_path, max_dimension=2048, bbox_wgs84=load_bbox
        )
    except Exception:
        return None, None
    if not bands:
        return None, None

    scale_factor = float(bands.pop("_scale_factor", 1.0))
    red = np.asarray(bands.get("RED"), dtype=np.float64)
    green = np.asarray(bands.get("GREEN"), dtype=np.float64)
    blue = np.asarray(bands.get("BLUE"), dtype=np.float64)
    if red is None or green is None or blue is None:
        return None, None

    rows, cols = red.shape
    scene_bounds: Optional[List[float]] = list(bands["_bounds"]) if bands.get("_bounds") and len(bands["_bounds"]) == 4 else None

    def _stretch_band(band: np.ndarray) -> np.ndarray:
        valid = band[band > 0]
        if valid.size == 0:
            return np.zeros_like(band, dtype=np.uint8)
        lo = float(np.percentile(valid, 2))
        hi = float(np.percentile(valid, 98))
        if hi <= lo:
            hi = lo + 1e-6
        return np.clip((band - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)

    scene_rgb = np.stack(
        [_stretch_band(red), _stretch_band(green), _stretch_band(blue)], axis=-1
    )

    bounds: List[float]
    rgb: np.ndarray

    if display_bbox and scene_bounds and len(scene_bounds) == 4:
        disp_west, disp_south, disp_east, disp_north = display_bbox
        deg_w = max(1e-6, disp_east - disp_west)
        deg_h = max(1e-6, disp_north - disp_south)
        out_w, out_h = 2048, 2048
        out_rgba = np.zeros((out_h, out_w, 4), dtype=np.uint8)

        s_s, w_s, n_s, e_s = scene_bounds
        if e_s != w_s and n_s != s_s:
            j_ = np.arange(out_w, dtype=np.float64)
            i_ = np.arange(out_h, dtype=np.float64)
            lon_1d = disp_west + deg_w * (j_ + 0.5) / out_w
            lat_2d = disp_north - deg_h * (i_[:, None] + 0.5) / out_h

            sx = np.clip(
                ((lon_1d - w_s) / (e_s - w_s) * (cols - 1)).astype(np.int32), 0, cols - 1
            )
            sy = np.clip(
                ((n_s - lat_2d) / (n_s - s_s) * (rows - 1)).astype(np.int32), 0, rows - 1
            )
            sj = np.broadcast_to(sx, (out_h, out_w))
            si = np.broadcast_to(sy, (out_h, out_w))
            out_rgba[:, :, :3] = scene_rgb[si, sj, :]
            out_rgba[:, :, 3] = 255

        rgb = out_rgba
        bounds = [disp_south, disp_west, disp_north, disp_east]
    else:
        rgb = scene_rgb
        if scene_bounds and len(scene_bounds) == 4:
            s, w, n, e = scene_bounds
            bounds = [s, w, n, e]
        elif display_bbox:
            w, s, e, n = display_bbox
            bounds = [s, w, n, e]
        else:
            clon = getattr(area, "center_lon", None) or 139.1147
            clat = getattr(area, "center_lat", None) or 34.9656
            pixel_size_m = 10.0 * scale_factor
            mn_lon, ms_lat, mx_lon, my_lat = raster_bounds_from_center(rows, cols, clon, clat, pixel_size_m)
            bounds = [ms_lat, mn_lon, my_lat, mx_lon]

    try:
        from PIL import Image
    except ImportError:
        return None, None

    mode = "RGBA" if rgb.shape[-1] == 4 else "RGB"
    img = Image.fromarray(rgb, mode=mode)
    rel_dir = f"detection_results/{result_id}"
    media_dir = Path(settings.MEDIA_ROOT) / rel_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    img.save(media_dir / "map.png", "PNG")
    return f"{rel_dir}/map.png", bounds
