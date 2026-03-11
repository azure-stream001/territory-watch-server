"""
Sentinel-2 製品（zip/SAFE）から RED/GREEN/BLUE/NIR を読み、パイプライン用の辞書を返す。
失敗時は BandLoadError を発生させる（原因を保持）。
"""
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.conf import settings


class BandLoadError(Exception):
    """バンド読み込み失敗。detail に技術的な原因を保持。"""
    def __init__(self, message: str, detail: Optional[str] = None):
        self.detail = detail
        full = f"{message} ({detail})" if detail else message
        super().__init__(full)


def resolve_scene_path(file_path: str) -> Path:
    """Scene.file_path（相対または絶対）を絶対 Path に変換。"""
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / file_path
    return p


# 10m バンド: L2A の .jp2 ファイル名サフィックス
_SUFFIX_BAND = [
    ("_B02_10m.jp2", "BLUE"),
    ("_B03_10m.jp2", "GREEN"),
    ("_B04_10m.jp2", "RED"),
    ("_B08_10m.jp2", "NIR"),
]


def _find_band_paths_in_zip(zip_path: Path) -> Dict[str, str]:
    """Zip 内で B02/B03/B04/B08 の 10m jp2 を探す。1つの GRANULE 内のバンドだけを使う（混在を防ぐ）。"""
    # Group by granule: path like .../GRANULE/L2A_T54SUD_.../IMG_DATA/R10m/...
    granule_bands: Dict[str, Dict[str, str]] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        uri = zip_path.as_uri()
        if not uri.startswith("file://"):
            uri = "file://" + uri
        for name in zf.namelist():
            if not name.endswith(".jp2"):
                continue
            if "R10m" not in name:
                continue
            for suf, key in _SUFFIX_BAND:
                if suf in name:
                    # Use granule path (up to IMG_DATA/R10m) as key so we keep one granule
                    parts = name.split("/")
                    try:
                        idx = parts.index("GRANULE")
                        granule_key = "/".join(parts[: idx + 2]) if idx + 2 <= len(parts) else name
                    except ValueError:
                        granule_key = name
                    if granule_key not in granule_bands:
                        granule_bands[granule_key] = {}
                    granule_bands[granule_key][key] = f"zip+{uri}!{name}"
                    break
    # Pick one granule that has all 4 bands
    for band_dict in granule_bands.values():
        if len(band_dict) == 4:
            return band_dict
    return {}


def _find_band_paths_safe_dir(safe_dir: Path) -> Dict[str, str]:
    """展開済み .SAFE ディレクトリ内でバンドファイルを探す。"""
    band_files: Dict[str, str] = {}
    for jp2 in safe_dir.rglob("*.jp2"):
        name = jp2.name
        for suf, key in _SUFFIX_BAND:
            if suf in name:
                band_files[key] = str(jp2)
                break
    return band_files


def _bounds_to_wgs84(left: float, bottom: float, right: float, top: float, src_crs) -> list:
    """Transform raster bounds from source CRS to WGS84 [south, west, north, east]."""
    import rasterio.warp

    if src_crs is None or src_crs.is_geographic:
        # Already degrees or unknown; assume WGS84-like
        return [bottom, left, top, right]
    min_lon, min_lat, max_lon, max_lat = rasterio.warp.transform_bounds(
        src_crs, "EPSG:4326", left, bottom, right, top
    )
    return [min_lat, min_lon, max_lat, max_lon]


def _compute_crop_window(src, bbox_wgs84: Optional[Tuple[float, float, float, float]]):
    """
    Given an open rasterio dataset and a WGS84 bbox (west, south, east, north),
    return a rasterio.windows.Window cropped to the bbox, or None for the full raster.
    """
    if bbox_wgs84 is None:
        return None
    import rasterio.warp
    import rasterio.windows

    west, south, east, north = bbox_wgs84
    # Add a 10% buffer so the area is not clipped right to its edge
    dlon = (east - west) * 0.10
    dlat = (north - south) * 0.10
    west -= dlon; east += dlon; south -= dlat; north += dlat

    # Transform bbox from WGS84 to raster CRS
    if src.crs and not src.crs.is_geographic:
        try:
            xs, ys = rasterio.warp.transform("EPSG:4326", src.crs, [west, east], [south, north])
            left, right = min(xs), max(xs)
            bottom, top = min(ys), max(ys)
        except Exception:
            return None
    else:
        left, right, bottom, top = west, east, south, north

    try:
        window = rasterio.windows.from_bounds(left, bottom, right, top, transform=src.transform)
        # Clamp to actual raster extent
        window = window.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        if window.width < 4 or window.height < 4:
            return None
        return window
    except Exception:
        return None


def _read_bands_from_paths(
    band_files: Dict[str, str],
    abs_path: Path,
    max_dimension: int = 4096,
    bbox_wgs84: Optional[Tuple[float, float, float, float]] = None,
) -> Dict[str, Any]:
    """band_files の URI/パスを rasterio で開き、配列辞書を返す。失敗時は BandLoadError。
    bbox_wgs84 が指定された場合はその WGS84 領域だけをフル解像度で読み込む（高画質）。
    bbox_wgs84 が None の場合は max_dimension でダウンサンプル。
    _bounds は WGS84 [south, west, north, east] で返す。"""
    import numpy as np
    import rasterio
    import rasterio.windows
    from rasterio.transform import array_bounds as _array_bounds

    if len(band_files) != 4:
        raise BandLoadError(
            f"10m バンドが4つ見つかりません（{list(band_files.keys())}）。"
            " L2A の zip 内に IMG_DATA/R10m/*_B02_10m.jp2 等があるか確認してください。",
            detail=f"found_bands={list(band_files.keys())}",
        )

    def _read_one_windowed(loc: str, window, out_shape: Optional[Tuple[int, int]] = None):
        with rasterio.open(loc) as src:
            if window is not None:
                data = src.read(1, window=window, out_shape=out_shape)
            elif out_shape is not None:
                data = src.read(1, out_shape=out_shape)
            else:
                data = src.read(1)
            return data

    def _to_float_dict(arrays: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for k, arr in arrays.items():
            a = np.asarray(arr, dtype=np.float64)
            if a.max() > 1.5:
                a = np.clip(a / 10000.0, 0, 1)
            out[k] = a
        return out

    # まず zip+file:// で開く
    last_err: Optional[str] = None
    try:
        first_loc = next(iter(band_files.values()))
        with rasterio.open(first_loc) as src:
            window = _compute_crop_window(src, bbox_wgs84)
            if window is not None:
                # Crop to area bbox at full resolution
                win_h = int(window.height)
                win_w = int(window.width)
                # If still very large, cap it
                if max_dimension > 0 and (win_h > max_dimension or win_w > max_dimension):
                    scale = min(max_dimension / win_h, max_dimension / win_w, 1.0)
                    out_shape: Optional[Tuple[int, int]] = (int(win_h * scale), int(win_w * scale))
                    scale_factor = 1.0 / scale
                else:
                    out_shape = None
                    scale_factor = 1.0
                # Actual bounds for cropped region
                window_transform = rasterio.windows.transform(window, src.transform)
                crop_h = out_shape[0] if out_shape else win_h  # noqa: F841
                crop_w = out_shape[1] if out_shape else win_w  # noqa: F841
                b_left, b_bottom, b_right, b_top = _array_bounds(win_h, win_w, window_transform)
                raster_bounds_swne = _bounds_to_wgs84(b_left, b_bottom, b_right, b_top, src.crs)
            else:
                # Full raster with optional downsample
                h, w = src.height, src.width
                b = src.bounds
                raster_bounds_swne = _bounds_to_wgs84(b.left, b.bottom, b.right, b.top, src.crs)
                scale = 1.0
                if max_dimension > 0 and (h > max_dimension or w > max_dimension):
                    scale = min(max_dimension / h, max_dimension / w, 1.0)
                out_shape = (int(h * scale), int(w * scale)) if scale < 1.0 else None
                scale_factor = 1.0 / scale if scale < 1.0 else 1.0

        arrays = {}
        for key, loc in band_files.items():
            arrays[key] = _read_one_windowed(loc, window, out_shape)
        result = _to_float_dict(arrays)
        if scale_factor != 1.0:
            result["_scale_factor"] = scale_factor
        result["_bounds"] = raster_bounds_swne
        return result
    except Exception as e:
        last_err = f"zip URI で開けず: {e!s}"

    # フォールバック: zip を一時展開して読み込む
    if abs_path.suffix.lower() == ".zip":
        try:
            with tempfile.TemporaryDirectory(prefix="sentinel_") as tmp:
                with zipfile.ZipFile(abs_path, "r") as zf:
                    zf.extractall(tmp)
                tmp_path = Path(tmp)
                candidates = list(tmp_path.glob("*.SAFE"))
                if not candidates:
                    candidates = [d for d in tmp_path.iterdir() if d.is_dir()]
                for safe in candidates:
                    found = _find_band_paths_safe_dir(safe)
                    if len(found) != 4:
                        continue
                    first_p = next(iter(found.values()))
                    with rasterio.open(first_p) as src:
                        window_fb = _compute_crop_window(src, bbox_wgs84)
                        if window_fb is not None:
                            win_h = int(window_fb.height)
                            win_w = int(window_fb.width)
                            if max_dimension > 0 and (win_h > max_dimension or win_w > max_dimension):
                                scale = min(max_dimension / win_h, max_dimension / win_w, 1.0)
                                out_shape_fb: Optional[Tuple[int, int]] = (int(win_h * scale), int(win_w * scale))
                                scale_fb = 1.0 / scale
                            else:
                                out_shape_fb = None
                                scale_fb = 1.0
                            window_transform = rasterio.windows.transform(window_fb, src.transform)
                            b_left, b_bottom, b_right, b_top = _array_bounds(win_h, win_w, window_transform)
                            raster_bounds_swne = _bounds_to_wgs84(b_left, b_bottom, b_right, b_top, src.crs)
                        else:
                            h, w = src.height, src.width
                            b = src.bounds
                            raster_bounds_swne = _bounds_to_wgs84(b.left, b.bottom, b.right, b.top, src.crs)
                            scale = 1.0
                            if max_dimension > 0 and (h > max_dimension or w > max_dimension):
                                scale = min(max_dimension / h, max_dimension / w, 1.0)
                            out_shape_fb = (int(h * scale), int(w * scale)) if scale < 1.0 else None
                            scale_fb = 1.0 / scale if scale < 1.0 else 1.0
                    arrays = {}
                    for key, p in found.items():
                        with rasterio.open(p) as src:
                            arrays[key] = _read_one_windowed(str(p), window_fb, out_shape_fb)
                    out = _to_float_dict(arrays)
                    if scale_fb != 1.0:
                        out["_scale_factor"] = scale_fb
                    out["_bounds"] = raster_bounds_swne
                    return out
                raise BandLoadError(
                    "zip を展開しましたが、4バンドの .jp2 が見つかりません。"
                    " 製品が L2A の 10m バンド（B02/B03/B04/B08）を含むか確認してください。",
                    detail=last_err,
                )
        except BandLoadError:
            raise
        except Exception as e:
            raise BandLoadError(
                f"zip 展開後の読み込みに失敗しました: {e!s}",
                detail=last_err,
            ) from e

    raise BandLoadError(
        "バンドを開けませんでした。",
        detail=last_err,
    )


def load_bands_from_sentinel_product(
    file_path: str,
    max_dimension: int = 4096,
    bbox_wgs84: Optional[Tuple[float, float, float, float]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Sentinel-2 L2A の zip または .SAFE から 10m バンドを読み、RED/GREEN/BLUE/NIR の辞書を返す。

    bbox_wgs84: (west, south, east, north) in WGS84 degrees.
        指定するとその領域だけをフル解像度で読む（高画質・小メモリ）。
        None の場合は全シーンを max_dimension でダウンサンプル。
    max_dimension: bbox 指定時もこの上限を超えるなら縮小（デフォルト 4096、0 で無制限）。
    読み込み失敗時は BandLoadError を発生させる（原因を保持）。
    """
    try:
        import rasterio  # noqa: F401
    except ImportError as e:
        raise BandLoadError("rasterio がインストールされていません。", detail=str(e))

    abs_path = resolve_scene_path(file_path)
    if not abs_path.exists():
        raise BandLoadError(
            f"シーンファイルが存在しません: {abs_path}",
            detail=f"resolved={abs_path}, MEDIA_ROOT={getattr(settings, 'MEDIA_ROOT', None)}",
        )

    band_files: Dict[str, str] = {}
    if abs_path.suffix.lower() == ".zip":
        band_files = _find_band_paths_in_zip(abs_path)
    else:
        safe = abs_path if abs_path.is_dir() else abs_path.parent
        if safe.is_dir():
            band_files = _find_band_paths_safe_dir(safe)

    return _read_bands_from_paths(band_files, abs_path, max_dimension=max_dimension, bbox_wgs84=bbox_wgs84)
