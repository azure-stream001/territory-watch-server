"""
Territory Watch Japan - 仕様書に基づくパイプライン統合.
前処理 → 変化検出 → 違法性判定 → 精度評価 → GeoJSON 出力。
衛星シーンは DB で管理し、Before/After 両方ある場合のみ比較実行；片方だけなら取得して保存し次回用とする。
"""
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from .preprocessing import PreprocessingPipeline
from .change_detection import ChangeDetection
from .illegality import IllegalityDetector
from .accuracy import AccuracyAssessment
from .geo_utils import filter_geojson_to_area, labeled_to_geojson_polygons
from .services import (
    find_scene_earliest_in_window,
    find_scene_latest_in_window,
    get_or_fetch_scene,
    fetch_scene_in_window,
)
from .io_sentinel import load_bands_from_sentinel_product

# 比較を行わず「次回用に保存した」ことを示す戻り値
SAVED_FOR_NEXT = ("saved_for_next_time", "")


def _load_bands_for_scene(
    scene: Any,
    bbox_wgs84: Optional[Tuple[float, float, float, float]] = None,
) -> Optional[Dict[str, Any]]:
    """SatelliteScene から RED/GREEN/BLUE/NIR を読み、失敗時は None。bbox_wgs84 で領域を限定可能。"""
    if not scene or not scene.file_path:
        return None
    return load_bands_from_sentinel_product(scene.file_path, bbox_wgs84=bbox_wgs84)


def _bounds_for_crop(
    original_bounds: Optional[List[float]],
    original_rows: int,
    original_cols: int,
    crop_rows: int,
    crop_cols: int,
) -> Optional[List[float]]:
    """
    Return geographic bounds [south, west, north, east] for the cropped top-left
    (crop_rows x crop_cols) of a raster with original shape and bounds.
    Raster convention: row 0 = north, col 0 = west.
    """
    if not original_bounds or len(original_bounds) != 4:
        return original_bounds
    south, west, north, east = original_bounds
    if original_rows <= 0 or original_cols <= 0:
        return original_bounds
    # Cropped region is [0:crop_rows, 0:crop_cols]; same north/west, shrink east/south
    frac_w = crop_cols / original_cols
    frac_h = crop_rows / original_rows
    east_c = west + (east - west) * frac_w
    south_c = north + (south - north) * frac_h
    return [south_c, west, north, east_c]


def _ensure_same_shape(
    before_bands: Dict[str, Any],
    after_bands: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """2つのバンド辞書を同じ shape に揃える（最小サイズでクロップ）。クロップ時は _bounds を実範囲に更新する。"""
    import numpy as np
    r1 = before_bands.get("RED")
    r2 = after_bands.get("RED")
    if r1 is None or r2 is None:
        return before_bands, after_bands
    if not hasattr(r1, "shape") or not hasattr(r2, "shape"):
        return before_bands, after_bands
    if r1.shape == r2.shape:
        return before_bands, after_bands
    h, w = min(r1.shape[0], r2.shape[0]), min(r1.shape[1], r2.shape[1])
    meta_keys = {"_bounds", "_scale_factor"}

    def crop(d: Dict[str, Any], orig_rows: int, orig_cols: int) -> Dict[str, Any]:
        out = {}
        for k, v in d.items():
            if k in meta_keys:
                out[k] = v
            elif hasattr(v, "shape") and len(getattr(v, "shape", ())) >= 2:
                out[k] = np.asarray(v)[:h, :w]
            else:
                out[k] = v
        # Update _bounds to the cropped raster extent so pixel→geo is correct
        if "_bounds" in out and isinstance(out["_bounds"], (list, tuple)):
            new_bounds = _bounds_for_crop(
                list(out["_bounds"]), orig_rows, orig_cols, h, w
            )
            if new_bounds is not None:
                out["_bounds"] = new_bounds
        return out

    return crop(before_bands, r1.shape[0], r1.shape[1]), crop(after_bands, r2.shape[0], r2.shape[1])


def run_pipeline(
    job: Any,
) -> Union[
    Tuple[Dict[str, Any], Dict[str, Any], List[Dict], Dict[str, Any]],
    Tuple[Literal["saved_for_next_time"], str],
]:
    """
    DetectionJob を受け取り、実データ（Sentinel-2）で変化検出を実行する。
    - Before/After の日付が必須。指定がない場合は RuntimeError。
    - 両シーンが揃っていれば比較を実行し (geojson, metrics, violations, accuracy) を返す。
    - 片方でも欠けていれば取得を試みて保存し、("saved_for_next_time", message) を返す（結果は作らない）。
    """
    area = job.area
    params = job.params or {}
    threshold_ndvi = float(params.get("ndvi_threshold", -0.3))
    min_area_ha = float(params.get("min_area_ha", 1.0))
    max_cloud_coverage = max(0, min(100, int(params.get("max_cloud_coverage", 10))))
    cloud_cover = (0, max_cloud_coverage)
    pixel_size_m = 10.0
    min_area_pixels = max(1, int(min_area_ha * 10000 / (pixel_size_m ** 2)))

    before_start = getattr(job, "before_date_start", None)
    before_end = getattr(job, "before_date_end", None)
    after_start = getattr(job, "after_date_start", None)
    after_end = getattr(job, "after_date_end", None)

    if not before_start or not after_end:
        raise RuntimeError(
            "検出期間（Before 開始日 と After 終了日）を指定してください。"
        )
    if after_end < before_start:
        raise RuntimeError("After 終了日は Before 開始日以降にしてください。")

    # Fill in missing sub-window bounds
    if not before_end:
        before_end = after_end
    if not after_start:
        after_start = before_start

    # Require distinct periods: Before must end strictly before After starts.
    # When serializer defaults make windows identical (before_end=after_end, after_start=before_start),
    # or when ranges overlap, split at midpoint so we request two different dates.
    _timedelta = __import__("datetime").timedelta
    if before_end >= after_start:
        total_days = (after_end - before_start).days
        half = max(0, total_days // 2)
        before_end = before_start + _timedelta(days=half)
        after_start = before_end + _timedelta(days=1)

    # Search DB first for each period independently
    scene_before = find_scene_earliest_in_window(area, before_start, before_end)
    scene_after = find_scene_latest_in_window(area, after_start, after_end)

    # Download if missing — use the specific window and cloud cover from job params
    if not scene_before or scene_before.status != "downloaded":
        scene_before = fetch_scene_in_window(
            area, before_start, before_end, cloud_cover=cloud_cover, prefer_earliest=True
        )
    if not scene_after or scene_after.status != "downloaded":
        scene_after = fetch_scene_in_window(
            area, after_start, after_end, cloud_cover=cloud_cover, prefer_earliest=False
        )

    # Guard: must not be the same scene (would produce a trivial comparison)
    if (
        scene_before
        and scene_after
        and scene_before.status == "downloaded"
        and scene_after.status == "downloaded"
        and scene_before.id == scene_after.id
    ):
        return SAVED_FOR_NEXT[0], (
            "Before と After に同じシーンが選ばれました。"
            " Before 終了日と After 開始日が重ならないよう期間を広げて再実行してください。"
        )

    # まだ片方でも DOWNLOADED でない → 次回用に保存した旨を返す
    if not scene_before or scene_before.status != "downloaded":
        msg = f"Before 期間（{before_start} ～ {before_end}）のシーン取得に失敗しました。"
        if scene_before and scene_before.status == "failed":
            msg = f"Before シーン取得失敗: {scene_before.error_message or 'No product'}"
        return SAVED_FOR_NEXT[0], msg
    if not scene_after or scene_after.status != "downloaded":
        msg = f"After 期間（{after_start} ～ {after_end}）のシーン取得に失敗しました。"
        if scene_after and scene_after.status == "failed":
            msg = f"After シーン取得失敗: {scene_after.error_message or 'No product'}"
        return SAVED_FOR_NEXT[0], msg

    # Load full product (no bbox). Cropping to area in UTM often gives a narrow strip (1 column),
    # which would make detection and maps show only one column; full load keeps proper resolution.
    before_bands = _load_bands_for_scene(scene_before)
    after_bands = _load_bands_for_scene(scene_after)
    scale_factor = max(
        float(before_bands.pop("_scale_factor", 1.0)),
        float(after_bands.pop("_scale_factor", 1.0)),
    )
    before_bands, after_bands = _ensure_same_shape(before_bands, after_bands)

    # ダウンサンプル時はピクセルサイズと最小面積を補正
    pixel_size_m = 10.0 * scale_factor
    min_area_pixels = max(1, int(min_area_ha * 10000 / (pixel_size_m ** 2)))

    geojson, metrics, violations, accuracy = _run_comparison(
        job, area, before_bands, after_bands,
        threshold_ndvi, min_area_pixels, pixel_size_m,
    )
    return geojson, metrics, violations, accuracy, scene_before.id, scene_after.id


def _run_comparison(
    job: Any,
    area: Any,
    before_bands: Dict[str, Any],
    after_bands: Dict[str, Any],
    threshold_ndvi: float,
    min_area_pixels: int,
    pixel_size_m: float,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict], Dict[str, Any]]:
    """共通の比較フロー。"""
    target_area = {
        "name": area.name,
        "coords": [area.center_lon or 139.1147, area.center_lat or 34.9656],
        "buffer_radius": 2000,
    }
    preprocessor = PreprocessingPipeline(target_area)
    before_bands = preprocessor.atmospheric_correction(before_bands)
    after_bands = preprocessor.atmospheric_correction(after_bands)
    before_indices = preprocessor.calculate_indices(before_bands)
    after_indices = preprocessor.calculate_indices(after_bands)
    before_ndvi = before_indices["NDVI"]
    after_ndvi = after_indices["NDVI"]

    baseline_year = job.before_date_start.year if getattr(job, "before_date_start", None) else 2017
    target_year = (
        job.after_date_end.year
        if getattr(job, "after_date_end", None)
        else (job.after_date_start.year if getattr(job, "after_date_start", None) else 2019)
    )
    detector = ChangeDetection(
        baseline_year=baseline_year,
        target_year=target_year,
        threshold_ndvi_drop=threshold_ndvi,
    )
    deforestation_binary, labeled = detector.detect_deforestation(
        before_ndvi, after_ndvi, min_area_pixels=min_area_pixels
    )
    pattern = detector.analyze_pattern(labeled, pixel_size_m=pixel_size_m)

    metrics = {
        "detected_area_ha": pattern["total_area_ha"],
        "num_patches": pattern["num_patches"],
        "fragmentation_index": pattern["fragmentation_index"],
        "patch_areas_ha": pattern["patch_areas_ha"],
    }

    illegality = IllegalityDetector(
        area_threshold_ha=49,
        combined_min_ha=50,
        proximity_threshold_m=100,
    )
    development_areas = pattern.get("development_areas", [])
    violations = illegality.detect_split_applications(development_areas)
    for dev_area in development_areas:
        slope_result = illegality.check_slope_violation(dev_area, None)
        if slope_result.get("violation"):
            violations.append(slope_result)

    accuracy_assessor = AccuracyAssessment(ground_truth=None)
    actual_area_ha = 105.0
    accuracy = accuracy_assessor.evaluate(
        deforestation_binary,
        detected_area_ha=metrics["detected_area_ha"],
        actual_area_ha=actual_area_ha,
    )

    # Use raster bounds center so pixel→geo matches the actual cropped raster (result map extent)
    center_lon = area.center_lon or 139.1147
    center_lat = area.center_lat or 34.9656
    if isinstance(after_bands.get("_bounds"), (list, tuple)) and len(after_bands["_bounds"]) == 4:
        south, west, north, east = after_bands["_bounds"]
        center_lon = (west + east) / 2.0
        center_lat = (south + north) / 2.0
    deforestation_geojson = labeled_to_geojson_polygons(
        labeled,
        center_lon=center_lon,
        center_lat=center_lat,
        pixel_size_m=pixel_size_m,
    )
    # 検出エリア（緑ポリゴン）外に赤表示が出ないよう、地域 footprint 内のものだけに限定する
    footprint_wkt = (getattr(area, "footprint_wkt", None) or "").strip()
    deforestation_geojson = filter_geojson_to_area(deforestation_geojson, footprint_wkt or None)
    return deforestation_geojson, metrics, violations, accuracy
