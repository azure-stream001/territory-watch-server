"""
Generate a preview RGB image and tile pyramid for a SatelliteScene from its downloaded Sentinel-2 product.
Each scene uses its own file_path; cache is invalidated when file_path changes.
Tiles allow correct imagery when the user zooms or pans the map.
"""
import json
import math
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from django.conf import settings

from .geo_utils import area_bbox_wgs84, polygon_center_and_crop_bbox, raster_bounds_from_center
from .io_sentinel import load_bands_from_sentinel_product
from .models import SatelliteScene


def get_scene_preview_paths(scene_id: int) -> Tuple[Path, Path]:
    """Return (media_dir, map_path) for scene preview cache."""
    rel_dir = Path(settings.MEDIA_ROOT) / "scene_previews" / str(scene_id)
    return rel_dir, rel_dir / "map.png"


def _tile_bounds(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Return (south, west, north, east) in degrees for Web Mercator tile (z, x, y)."""
    n = 2.0 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    south_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n)))
    north = math.degrees(north_rad)
    south = math.degrees(south_rad)
    return (south, west, north, east)


def _tile_range_for_bounds(
    bounds: List[float], z: int
) -> Tuple[int, int, int, int]:
    """Return (x_min, x_max, y_min, y_max) tile indices at zoom z that overlap bounds [south, west, north, east]."""
    south, west, north, east = bounds
    n = 2.0 ** z
    x_min = max(0, int((west + 180.0) / 360.0 * n))
    x_max = min(int((east + 180.0) / 360.0 * n), int(n) - 1)
    def lat_to_y(lat_deg: float) -> int:
        lat_rad = math.radians(lat_deg)
        y = n / 2.0 * (1.0 - math.asinh(math.tan(lat_rad)) / math.pi)
        return max(0, min(int(n) - 1, int(y)))

    y_min = lat_to_y(north)
    y_max = lat_to_y(south)
    if y_min > y_max:
        y_min, y_max = y_max, y_min
    return (x_min, x_max, y_min, y_max)


def _generate_tiles(
    rgb: np.ndarray,
    bounds: List[float],
    media_dir: Path,
    z_min: int = 10,
    z_max: int = 16,
    tile_size: int = 256,
    tile_range_bounds: Optional[List[float]] = None,
) -> None:
    """
    Generate Web Mercator tile pyramid from RGB or RGBA array.
    rgb: (H, W, 3) or (H, W, 4) uint8. bounds: [south, west, north, east] (image extent).
    When tile_range_bounds is set, generate tiles for that full area (transparent where no image)
    so the map never 404s; otherwise use bounds for the tile range.
    Saves tiles to media_dir/tiles/{z}/{x}/{y}.png as RGBA (transparent outside image area).
    """
    try:
        from PIL import Image
    except ImportError:
        return
    H, W = rgb.shape[0], rgb.shape[1]
    south, west, north, east = bounds
    range_bounds = tile_range_bounds if tile_range_bounds and len(tile_range_bounds) == 4 else bounds
    has_alpha = rgb.shape[-1] == 4
    mode = "RGBA" if has_alpha else "RGB"

    def lon_to_src_x(lon: float) -> float:
        return (lon - west) / (east - west) * W if east != west else 0.0

    def lat_to_src_y(lat: float) -> float:
        return (north - lat) / (north - south) * H if north != south else 0.0

    def lon_to_tile_x(lon: float, t_west: float, t_east: float) -> float:
        return (lon - t_west) / (t_east - t_west) * tile_size if t_east != t_west else 0.0

    def lat_to_tile_y(lat: float, t_south: float, t_north: float) -> float:
        return (t_north - lat) / (t_north - t_south) * tile_size if t_north != t_south else 0.0

    src_image = Image.fromarray(rgb, mode=mode)

    for z in range(z_min, z_max + 1):
        x_min, x_max, y_min, y_max = _tile_range_for_bounds(range_bounds, z)
        tile_dir = media_dir / "tiles" / str(z)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                t_south, t_west, t_north, t_east = _tile_bounds(z, x, y)

                ol_west = max(west, t_west)
                ol_east = min(east, t_east)
                ol_south = max(south, t_south)
                ol_north = min(north, t_north)
                no_overlap = ol_east <= ol_west or ol_north <= ol_south

                if no_overlap:
                    tile_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
                    out_dir = tile_dir / str(x)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    tile_img.save(out_dir / f"{y}.png", "PNG")
                    continue

                sx0 = max(0, int(lon_to_src_x(ol_west)))
                sx1 = min(W, math.ceil(lon_to_src_x(ol_east)))
                sy0 = max(0, int(lat_to_src_y(ol_north)))
                sy1 = min(H, math.ceil(lat_to_src_y(ol_south)))
                if sx1 <= sx0 or sy1 <= sy0:
                    tile_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
                    out_dir = tile_dir / str(x)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    tile_img.save(out_dir / f"{y}.png", "PNG")
                    continue

                tx0 = max(0, int(lon_to_tile_x(ol_west, t_west, t_east)))
                tx1 = min(tile_size, math.ceil(lon_to_tile_x(ol_east, t_west, t_east)))
                ty0 = max(0, int(lat_to_tile_y(ol_north, t_south, t_north)))
                ty1 = min(tile_size, math.ceil(lat_to_tile_y(ol_south, t_south, t_north)))
                if tx1 <= tx0 or ty1 <= ty0:
                    tile_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
                    out_dir = tile_dir / str(x)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    tile_img.save(out_dir / f"{y}.png", "PNG")
                    continue

                patch = src_image.crop((sx0, sy0, sx1, sy1))
                target_w = tx1 - tx0
                target_h = ty1 - ty0
                if target_w < 1 or target_h < 1:
                    tile_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
                    out_dir = tile_dir / str(x)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    tile_img.save(out_dir / f"{y}.png", "PNG")
                    continue

                patch = patch.resize((target_w, target_h), Image.Resampling.LANCZOS)
                tile_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
                if patch.mode != "RGBA":
                    patch = patch.convert("RGBA")
                tile_img.paste(patch, (tx0, ty0), mask=patch.split()[-1] if patch.mode == "RGBA" else None)

                out_dir = tile_dir / str(x)
                out_dir.mkdir(parents=True, exist_ok=True)
                tile_img.save(out_dir / f"{y}.png", "PNG")


def generate_scene_preview_image(scene_id: int) -> Tuple[Optional[str], Optional[list]]:
    """
    Load this scene's Sentinel-2 product, create an RGB PNG and tile pyramid, save to media.

    アルゴリズム:
      1. ポリゴンの重心と「外接円半径」を計算する。
      2. load_bbox  = 重心 ± radius × 3.0 の正方形でラスタをクロップしてロードする。
         → 常にポリゴンを内包し、かつ全粒（full granule）を読まずに済む。
      3. display_bbox = 重心 ± radius × 1.5 の正方形でキャンバスを作り描画する。
         → load_bbox ⊇ display_bbox なので全ピクセルに画像データが存在する。
         → ポリゴンが常にキャンバス中央に収まる。
      4. bounds = display_bbox を返す → Leaflet が正しい位置に画像を配置する。

    Returns (relative_path, bounds) or (None, None) on failure.
    bounds is [south, west, north, east].
    """
    try:
        obj = SatelliteScene.objects.select_related("area").get(pk=scene_id)
    except SatelliteScene.DoesNotExist:
        return None, None
    if obj.status != "downloaded" or not obj.file_path:
        return None, None

    file_path = obj.file_path.strip()
    area = obj.area

    # Step 1: ポリゴン重心・半径から表示用・ロード用 bbox を決定
    display_bbox, load_bbox = polygon_center_and_crop_bbox(
        area, display_padding=1.5, load_padding=3.0
    )
    if display_bbox is None:
        display_bbox = area_bbox_wgs84(area, margin_fraction=0.10)
        load_bbox = area_bbox_wgs84(area, margin_fraction=0.50)

    # Step 2: load_bbox でクロップしてロード（ポリゴン周辺のみ、全粒読みを回避）
    try:
        bands = load_bands_from_sentinel_product(
            file_path, max_dimension=2048, bbox_wgs84=load_bbox
        )
    except Exception:
        return None, None
    if not bands:
        return None, None

    scale_factor = float(bands.pop("_scale_factor", 1.0))
    if not all(k in bands for k in ("RED", "GREEN", "BLUE")):
        return None, None
    red = np.asarray(bands["RED"], dtype=np.float64)
    green = np.asarray(bands["GREEN"], dtype=np.float64)
    blue = np.asarray(bands["BLUE"], dtype=np.float64)

    def _stretch_band(band: np.ndarray) -> np.ndarray:
        """Percentile-based contrast stretch: map 2nd–98th percentile to 0–255."""
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
    rows, cols = red.shape
    # ロード後の実際の地理範囲 [south, west, north, east]
    scene_bounds: Optional[List[float]] = list(bands["_bounds"]) if "_bounds" in bands else None

    try:
        from PIL import Image
    except ImportError:
        return None, None

    rel_dir = f"scene_previews/{scene_id}"
    media_dir = Path(settings.MEDIA_ROOT) / rel_dir
    media_dir.mkdir(parents=True, exist_ok=True)

    # Step 3: display_bbox サイズの RGBA キャンバスに描画
    #   load_bbox ⊇ display_bbox なので scene_bounds は display_bbox を内包する。
    #   → 全出力ピクセルに有効なラスタデータが存在し、黒抜きにならない。
    bounds: List[float]
    rgb: np.ndarray

    if display_bbox and scene_bounds and len(scene_bounds) == 4:
        disp_west, disp_south, disp_east, disp_north = display_bbox
        deg_w = max(1e-6, disp_east - disp_west)
        deg_h = max(1e-6, disp_north - disp_south)
        # 正方形 2048×2048 キャンバス（display_bbox の縦横比は load_padding に依らず正方形）
        out_w, out_h = 2048, 2048
        out_rgba = np.zeros((out_h, out_w, 4), dtype=np.uint8)

        s_s, w_s, n_s, e_s = scene_bounds
        if e_s != w_s and n_s != s_s:
            j_ = np.arange(out_w, dtype=np.float64)
            i_ = np.arange(out_h, dtype=np.float64)
            lon_1d = disp_west + deg_w * (j_ + 0.5) / out_w         # (out_w,)
            lat_2d = disp_north - deg_h * (i_[:, None] + 0.5) / out_h  # (out_h, 1)

            sx = np.clip(
                ((lon_1d - w_s) / (e_s - w_s) * (cols - 1)).astype(np.int32), 0, cols - 1
            )  # (out_w,)
            sy = np.clip(
                ((n_s - lat_2d) / (n_s - s_s) * (rows - 1)).astype(np.int32), 0, rows - 1
            )  # (out_h, 1)

            sj = np.broadcast_to(sx, (out_h, out_w))   # col index
            si = np.broadcast_to(sy, (out_h, out_w))   # row index

            out_rgba[:, :, :3] = scene_rgb[si, sj, :]
            out_rgba[:, :, 3] = 255

        rgb = out_rgba
        bounds = [disp_south, disp_west, disp_north, disp_east]
    else:
        # フォールバック: ポリゴン情報が取れないときはロードしたラスタをそのまま表示
        rgb = scene_rgb
        if scene_bounds and len(scene_bounds) == 4:
            s, w, n, e = scene_bounds
            bounds = [s, w, n, e]
        elif display_bbox:
            w, s, e, n = display_bbox
            bounds = [s, w, n, e]
        else:
            clon = area.center_lon or 139.1147
            clat = area.center_lat or 34.9656
            pixel_size_m = 10.0 * scale_factor
            mn_lon, ms_lat, mx_lon, my_lat = raster_bounds_from_center(rows, cols, clon, clat, pixel_size_m)
            bounds = [ms_lat, mn_lon, my_lat, mx_lon]

    img = Image.fromarray(rgb, mode="RGBA" if rgb.shape[-1] == 4 else "RGB")
    img.save(media_dir / "map.png", "PNG")
    with open(media_dir / "bounds.json", "w") as f:
        json.dump({"bounds": bounds, "version": 2, "file_path": file_path}, f)

    _generate_tiles(rgb, bounds, media_dir)

    return f"{rel_dir}/map.png", bounds


def get_or_create_scene_preview(scene_id: int) -> Tuple[Optional[str], Optional[list]]:
    """
    Return (relative_path, bounds) for the scene preview.
    Cache is invalidated if the scene's file_path differs from the one used to generate the cache,
    so each scene shows imagery from its own product.
    """
    try:
        obj = SatelliteScene.objects.get(pk=scene_id)
    except SatelliteScene.DoesNotExist:
        return None, None
    if obj.status != "downloaded" or not obj.file_path:
        return None, None

    media_dir, map_path = get_scene_preview_paths(scene_id)
    bounds_path = media_dir / "bounds.json"
    if map_path.exists() and bounds_path.exists():
        try:
            with open(bounds_path) as f:
                data = json.load(f)
            if data.get("version") == 2 and data.get("bounds"):
                b = data["bounds"]
                if (
                    len(b) == 4
                    and -90 <= b[0] <= 90
                    and -90 <= b[2] <= 90
                    and -180 <= b[1] <= 180
                    and -180 <= b[3] <= 180
                ):
                    cached_path = data.get("file_path") or ""
                    if cached_path.strip() == obj.file_path.strip():
                        return f"scene_previews/{scene_id}/map.png", data["bounds"]
                shutil.rmtree(media_dir, ignore_errors=True)
        except (json.JSONDecodeError, OSError):
            pass
    return generate_scene_preview_image(scene_id)


def scene_has_tiles(scene_id: int) -> bool:
    """Return True if tiles exist for this scene (so frontend can use tile layer)."""
    media_dir, _ = get_scene_preview_paths(scene_id)
    tiles_dir = media_dir / "tiles"
    return tiles_dir.is_dir() and any(tiles_dir.iterdir())
