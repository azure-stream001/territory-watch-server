"""
仕様書 3.2 変化検出アルゴリズム.
森林伐採エリアの検出: NDVI 差分、森林マスク、モルフォロジー、ラベリング。
"""
from typing import Any, Dict, List, Tuple

import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class ChangeDetection:
    """森林伐採検出エンジン（仕様書 3.2）"""

    def __init__(
        self,
        baseline_year: int = 2017,
        target_year: int = 2019,
        threshold_ndvi_drop: float = -0.3,
        forest_ndvi_min: float = 0.6,
    ):
        self.baseline = baseline_year
        self.target = target_year
        self.threshold_ndvi_drop = threshold_ndvi_drop
        self.forest_ndvi_min = forest_ndvi_min

    def detect_deforestation(
        self,
        before_ndvi: np.ndarray,
        after_ndvi: np.ndarray,
        min_area_pixels: int = 100,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        森林伐採エリアの検出（仕様書 3.2）.
        1. NDVI 差分  2. 森林マスク  3. 閾値  4. モルフォロジー  5. ラベリング
        戻り値: (binary_deforestation, labeled_areas)
        """
        ndvi_diff = after_ndvi.astype(np.float64) - before_ndvi.astype(np.float64)
        forest_mask = before_ndvi > self.forest_ndvi_min
        valid = np.isfinite(ndvi_diff) & np.isfinite(before_ndvi)
        deforestation = (
            (ndvi_diff < self.threshold_ndvi_drop) & forest_mask & valid
        ).astype(np.uint8)

        if CV2_AVAILABLE:
            deforestation = self._morphological_filtering(deforestation)
        num_labels, labeled = self._label_connected_components(
            deforestation, min_area_pixels
        )
        return deforestation, labeled

    def _morphological_filtering(self, binary: np.ndarray) -> np.ndarray:
        """ノイズ除去（閉操作＋開操作）"""
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel)
        return opened

    def _label_connected_components(
        self, binary: np.ndarray, min_area_pixels: int
    ) -> Tuple[int, np.ndarray]:
        """連続領域のラベリング。最小面積未満は 0 に。"""
        if CV2_AVAILABLE:
            num_labels, labeled, stats, _ = cv2.connectedComponentsWithStats(
                binary, connectivity=8
            )
            for i in range(1, num_labels):
                if stats[i, cv2.CC_STAT_AREA] < min_area_pixels:
                    labeled[labeled == i] = 0
            return num_labels, labeled
        # fallback: scipy.ndimage
        try:
            import scipy.ndimage as ndi
            labeled, num_labels = ndi.label(binary)
            for i in range(1, num_labels + 1):
                if np.sum(labeled == i) < min_area_pixels:
                    labeled[labeled == i] = 0
            return num_labels, labeled
        except ImportError:
            return 0, np.zeros_like(binary)

    def analyze_pattern(
        self,
        labeled: np.ndarray,
        pixel_size_m: float = 10.0,
    ) -> Dict[str, Any]:
        """開発パターンの分析（仕様書 3.2）"""
        total_pixels = int(np.sum(labeled > 0))
        area_m2 = total_pixels * (pixel_size_m ** 2)
        total_area_ha = area_m2 / 10000.0

        num_patches = int(len(np.unique(labeled)) - (1 if 0 in labeled else 0))
        if num_patches < 0:
            num_patches = 0

        # 簡易フラグメンテーション: パッチ数 / (1 + 総面積ha)
        fragmentation_index = num_patches / (1.0 + total_area_ha) if total_area_ha > 0 else 0

        # パッチごとの面積 (ha) と重心 (pixel xy)
        patch_areas_ha: List[float] = []
        patch_centroids_xy: List[Tuple[float, float]] = []
        if CV2_AVAILABLE:
            _, _, stats, centroids = cv2.connectedComponentsWithStats(
                (labeled > 0).astype(np.uint8), connectivity=8
            )
            for i in range(1, stats.shape[0]):
                patch_areas_ha.append(stats[i, cv2.CC_STAT_AREA] * (pixel_size_m ** 2) / 10000.0)
                patch_centroids_xy.append((float(centroids[i, 0]), float(centroids[i, 1])))
        else:
            for uid in np.unique(labeled):
                if uid == 0:
                    continue
                mask = labeled == uid
                patch_areas_ha.append(np.sum(mask) * (pixel_size_m ** 2) / 10000.0)
                ys, xs = np.where(mask)
                patch_centroids_xy.append((float(np.mean(xs)), float(np.mean(ys))))

        development_areas = [
            {"size_ha": round(a, 4), "centroid_xy": c}
            for a, c in zip(patch_areas_ha, patch_centroids_xy)
        ]

        return {
            "total_area_ha": round(total_area_ha, 4),
            "num_patches": num_patches,
            "fragmentation_index": round(fragmentation_index, 4),
            "patch_areas_ha": [round(a, 4) for a in patch_areas_ha],
            "development_areas": development_areas,
            "suspicious_division": None,
        }
