"""
Quick vegetation change detection.
Generates a 3-panel comparison image — Before RGB | After RGB | NDVI Change Map —
modelled on the Deforestation-Detection project output but using Sentinel-2 data.

Color coding of the change panel:
  Green (50, 185, 80)   — vegetation retained
  Red   (220, 50, 50)   — vegetation lost (deforestation)
  Blue  (65, 168, 255)  — vegetation gained (regrowth)
  Gray  (220, 220, 220) — no vegetation in either period
"""

import math
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
from django.conf import settings

from .geo_utils import area_bbox_wgs84, polygon_center_and_crop_bbox
from .io_sentinel import load_bands_from_sentinel_product

# ── Colour palette ───────────────────────────────────────────────────────────
_CLR_RETAIN = (50,  185,  80)   # green  — retained vegetation
_CLR_LOST   = (220,  50,  50)   # red    — deforested
_CLR_GAINED = ( 65, 168, 255)   # blue   — regrowth
_CLR_NONE   = (220, 220, 220)   # gray   — no vegetation
_CLR_BG     = (200, 200, 200)   # gray   — out-of-scene background


# ── Band loading ─────────────────────────────────────────────────────────────

def _stretch(band: np.ndarray) -> np.ndarray:
    """2nd–98th percentile contrast stretch → uint8."""
    arr = np.asarray(band, dtype=np.float64)
    valid = arr[arr > 0]
    if valid.size == 0:
        return np.zeros_like(arr, dtype=np.uint8)
    lo, hi = float(np.percentile(valid, 2)), float(np.percentile(valid, 98))
    if hi <= lo:
        hi = lo + 1e-6
    return np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)


def _load_scene(
    file_path: str, load_bbox
) -> Tuple[Optional[np.ndarray], Optional[list], Optional[np.ndarray]]:
    """
    Load a Sentinel-2 scene and return (rgb_uint8, scene_bounds, ndvi_float64).
    Returns (None, None, None) on any failure.
    """
    # クイック検出では、シーン全体を常に読み込み（bbox でのクロップは行わない）。
    # bbox クロップは UTM タイル境界の都合で極端に細いストリップになることがあり、
    # 3 パネル画像が「細い帯」になる原因となるため。
    try:
        bands = load_bands_from_sentinel_product(
            file_path, max_dimension=1024, bbox_wgs84=None
        )
    except Exception:
        return None, None, None
    if not bands:
        return None, None, None

    bands.pop("_scale_factor", None)
    sb = list(bands["_bounds"]) if "_bounds" in bands and len(bands["_bounds"]) == 4 else None
    if not sb:
        return None, None, None

    r, g, b, nir = bands.get("RED"), bands.get("GREEN"), bands.get("BLUE"), bands.get("NIR")
    if r is None or g is None or b is None:
        return None, None, None

    rgb = np.stack([_stretch(r), _stretch(g), _stretch(b)], axis=-1)

    ndvi: Optional[np.ndarray] = None
    if nir is not None:
        nir_f = np.asarray(nir, dtype=np.float64)
        r_f   = np.asarray(r,   dtype=np.float64)
        ndvi  = (nir_f - r_f) / (nir_f + r_f + 1e-9)

    return rgb, sb, ndvi


# ── Canvas rendering ──────────────────────────────────────────────────────────

# def _canvas_hw_for_bbox(display_bbox: tuple, base: int) -> Tuple[int, int]:
#     """
#     与えられた地理的 bbox（lon/lat）に対して、縦横比を維持したまま
#     最大辺が base ピクセルになるような (height, width) を返す。
#     """
#     dw, ds, de, dn = display_bbox
#     deg_w = max(1e-6, de - dw)
#     deg_h = max(1e-6, dn - ds)
#     # 経度方向を横、緯度方向を縦として比率を計算
#     if deg_w >= deg_h:
#         width = base
#         height = max(1, int(round(base * (deg_h / deg_w))))
#     else:
#         height = base
#         width = max(1, int(round(base * (deg_w / deg_h))))

#     return height, width
def _canvas_hw_for_bbox(display_bbox: tuple, base: int) -> Tuple[int, int]:
    """
    Fix height to `base`, scale width proportionally.
    This ensures all panels align horizontally (like flex row).
    """
    dw, ds, de, dn = display_bbox
    deg_w = max(1e-6, de - dw)
    deg_h = max(1e-6, dn - ds)

    height = base
    width = max(1, int(round(base * (deg_w / deg_h))))
    width = min(width, base * 3)

    return height, width


def _resample(
    src: np.ndarray,
    scene_bounds: list,
    display_bbox: tuple,
    base_size: int,
    background,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Resample src onto a fixed-height canvas preserving aspect ratio of `size` px
    at display_bbox extent.  Returns (canvas, coverage_mask) where coverage_mask is
    a boolean (size×size) array indicating pixels that have real data.
    """
    dw, ds, de, dn = display_bbox
    deg_w = max(1e-6, de - dw)
    deg_h = max(1e-6, dn - ds)
    s_s, w_s, n_s, e_s = scene_bounds
    rows, cols = src.shape[:2]

    # bbox の縦横比を維持したキャンバスサイズ（高さ, 幅）
    h, w = _canvas_hw_for_bbox(display_bbox, base_size)

    channels = src.shape[2] if src.ndim == 3 else None
    if channels:
        canvas = np.full((h, w, channels), background, dtype=src.dtype)
    else:
        canvas = np.full((h, w), background, dtype=src.dtype)
    coverage = np.zeros((h, w), dtype=bool)

    if e_s == w_s or n_s == s_s:
        return canvas, coverage

    j_ = np.arange(w, dtype=np.float64)
    i_ = np.arange(h, dtype=np.float64)
    lon_1d = dw + deg_w * (j_ + 0.5) / w
    lat_2d = dn - deg_h * (i_[:, None] + 0.5) / h

    mask = (
        np.broadcast_to((lon_1d >= w_s) & (lon_1d <= e_s), (h, w))
        & (lat_2d >= s_s)
        & (lat_2d <= n_s)
    )
    sj = np.broadcast_to(
        np.clip(((lon_1d - w_s) / (e_s - w_s) * (cols - 1)).astype(np.int32), 0, cols - 1),
        (h, w),
    )
    si = np.broadcast_to(
        np.clip(((n_s - lat_2d) / (n_s - s_s) * (rows - 1)).astype(np.int32), 0, rows - 1),
        (h, w),
    )
    if channels:
        canvas[mask] = src[si[mask], sj[mask]]
    else:
        canvas[mask] = src[si[mask], sj[mask]]
    coverage[mask] = True
    return canvas, coverage


def _add_label(img_arr: np.ndarray, text: str) -> np.ndarray:
    """Prepend a dark label bar above the image panel."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.fromarray(img_arr, "RGB")
        bar_h = 36
        bar = Image.new("RGB", (img_arr.shape[1], bar_h), (30, 30, 30))
        draw = ImageDraw.Draw(bar)
        font = None

        # ✅ 優先順位付きフォント読み込み
        font_paths = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # fallback
        ]

        for path in font_paths:
            try:
                font = ImageFont.truetype(path, 17)
                break
            except Exception:
                continue

        if font is None:
            font = ImageFont.load_default()
        draw.text((12, 8), text, fill=(255, 255, 255), font=font)
        out = Image.new("RGB", (img_arr.shape[1], img_arr.shape[0] + bar_h))
        out.paste(bar, (0, 0))
        out.paste(img, (0, bar_h))
        return np.array(out)
    except Exception:
        return img_arr


# ── Public API ────────────────────────────────────────────────────────────────

def generate_quick_detect_image(
    before_scene: Any,
    after_scene: Any,
    area: Any,
    veg_threshold: float = 0.3,
    panel_size: int = 512,
    before_year: int = 0,
    after_year: int = 0,
    before_label: str = "",
    after_label: str = "",
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Generate a 3-panel side-by-side comparison image:
      Panel 1 — Before satellite RGB
      Panel 2 — After satellite RGB
      Panel 3 — NDVI-based change map (red/green/blue/gray pixels)

    Uses the same centroid+radius bounding-box strategy as scene_preview.py so the
    polygon is always centred at the same scale in all three panels.

    Returns (relative_media_path, stats_dict) or (None, error_dict).
    """
    # ── 1. シーン読み込み ─────────────────────────────────────────────────────
    before_rgb, before_sb, before_ndvi = _load_scene(before_scene.file_path, None)
    if before_rgb is None:
        return None, {"error": "Before シーンのバンド読み込みに失敗しました"}

    after_rgb, after_sb, after_ndvi = _load_scene(after_scene.file_path, None)
    if after_rgb is None:
        return None, {"error": "After シーンのバンド読み込みに失敗しました"}

    # ── 2. 表示範囲 display_bbox を決定（「選択範囲 × 両シーンの共通部分」） ──
    # area からの候補 bbox
    display_bbox, _ = polygon_center_and_crop_bbox(
        area, display_padding=1.1, load_padding=1.5
    )
    if display_bbox is None:
        display_bbox = area_bbox_wgs84(area, margin_fraction=0.05)

    if not before_sb or not after_sb:
        if display_bbox is None:
            return None, {"error": "エリアとシーンの範囲が確定できません"}
    else:
        # display_bbox と両シーンの bounds の共通部分をとる
        bw_s, bs_s, bw_e, bs_n = before_sb[1], before_sb[0], before_sb[3], before_sb[2]
        aw_s, as_s, aw_e, as_n = after_sb[1], after_sb[0], after_sb[3], after_sb[2]

        if display_bbox is not None:
            dw, ds, de, dn = display_bbox
        else:
            dw, ds, de, dn = bw_s, bs_s, bw_e, bs_n

        iw = max(dw, bw_s, aw_s)
        is_ = max(ds, bs_s, as_s)
        ie = min(de, bw_e, aw_e)
        in_ = min(dn, bs_n, as_n)

        if ie <= iw or in_ <= is_:
            # 共通部分がない場合は元の display_bbox（または Before シーン）で妥協
            if display_bbox is None:
                display_bbox = (bw_s, bs_s, bw_e, bs_n)
        else:
            display_bbox = (iw, is_, ie, in_)

    if display_bbox is None:
        return None, {"error": "表示範囲が確定できません"}

    # ── render RGB panels ─────────────────────────────────────────────────────
    before_panel, before_cov = _resample(before_rgb, before_sb, display_bbox, panel_size, _CLR_BG)
    after_panel,  after_cov  = _resample(after_rgb,  after_sb,  display_bbox, panel_size, _CLR_BG)

    # ── vegetation masks ──────────────────────────────────────────────────────
    before_veg_raw = (before_ndvi > veg_threshold) if before_ndvi is not None else None
    after_veg_raw  = (after_ndvi  > veg_threshold) if after_ndvi  is not None else None

    # ── change map panel ──────────────────────────────────────────────────────
    # change_panel も display_bbox の縦横比に合わせる
    ch, cw = _canvas_hw_for_bbox(display_bbox, panel_size)
    change_panel = np.full((ch, cw, 3), _CLR_NONE, dtype=np.uint8)

    stats: Dict[str, Any] = {}

    if before_veg_raw is not None and after_veg_raw is not None:
        bv, _ = _resample(before_veg_raw.astype(np.uint8), before_sb, display_bbox, panel_size, 0)
        av, _ = _resample(after_veg_raw.astype(np.uint8),  after_sb,  display_bbox, panel_size, 0)
        bv = bv.astype(bool)
        av = av.astype(bool)

        both_covered = before_cov & after_cov

        retained = both_covered & bv &  av
        lost     = both_covered & bv & ~av
        gained   = both_covered & ~bv & av

        change_panel[retained] = _CLR_RETAIN
        change_panel[lost]     = _CLR_LOST
        change_panel[gained]   = _CLR_GAINED

        # ── statistics ───────────────────────────────────────────────────────
        dw, ds, de, dn = display_bbox
        deg_w = max(1e-6, de - dw)
        deg_h = max(1e-6, dn - ds)
        center_lat = (ds + dn) / 2.0
        m_lon = 111320.0 * math.cos(math.radians(center_lat))
        m_lat = 110540.0
        # change_panel のピクセルサイズに合わせて 1px あたりの面積を計算
        px_m2 = (deg_w * m_lon / cw) * (deg_h * m_lat / ch)

        n_lost   = int(lost.sum())
        n_gained = int(gained.sum())
        n_before_veg = int((both_covered & bv).sum())
        n_covered = int(both_covered.sum())

        lost_ha  = round(n_lost   * px_m2 / 10_000, 2)
        gain_ha  = round(n_gained * px_m2 / 10_000, 2)
        bef_ha   = round(n_before_veg * px_m2 / 10_000, 2)

        pct_lost = round(n_lost   / max(1, n_before_veg) * 100, 1)
        pct_gain = round(n_gained / max(1, n_covered)    * 100, 1)

        stats = {
            "percent_deforested":   pct_lost,
            "percent_regrowth":     pct_gain,
            "vegetation_lost_ha":   lost_ha,
            "vegetation_gained_ha": gain_ha,
            "before_vegetation_ha": bef_ha,
        }

    # ── panel labels ─────────────────────────────────────────────────────────
    if before_label:
        before_labeled = _add_label(before_panel, f"比較前（{before_label}）")
    else:
        by_str = f" ({before_year})" if before_year else ""
        before_labeled = _add_label(before_panel, f"比較前{by_str}")

    if after_label:
        after_labeled = _add_label(after_panel, f"比較後（{after_label}）")
    else:
        ay_str = f" ({after_year})" if after_year else ""
        after_labeled = _add_label(after_panel, f"比較後{ay_str}")
    change_labeled = _add_label(change_panel, "変化マップ")

    # Ensure all panels are the same height after labelling
    max_h = max(before_labeled.shape[0], after_labeled.shape[0], change_labeled.shape[0])

    def _pad(arr: np.ndarray) -> np.ndarray:
        if arr.shape[0] >= max_h:
            return arr
        pad = np.full((max_h - arr.shape[0], arr.shape[1], 3), _CLR_BG, dtype=np.uint8)
        return np.concatenate([arr, pad], axis=0)

    combined = np.concatenate(
        [_pad(before_labeled), _pad(after_labeled), _pad(change_labeled)], axis=1
    )

    # ── save image ────────────────────────────────────────────────────────────
    try:
        from PIL import Image
        img = Image.fromarray(combined, "RGB")
        rel_dir  = "quick_detect"
        media_dir = Path(settings.MEDIA_ROOT) / rel_dir
        media_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.png"
        img.save(media_dir / fname, "PNG")
        return f"{rel_dir}/{fname}", stats
    except ImportError:
        return None, {"error": "Pillow が見つかりません"}
