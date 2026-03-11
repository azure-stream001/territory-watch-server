"""
仕様書 3.1 前処理パイプライン.
衛星画像の前処理: 雲マスク、植生指数（NDVI, EVI, NDWI, SAVI）計算。
"""
from typing import Any, Dict, Optional, Tuple

import numpy as np


class PreprocessingPipeline:
    """衛星画像の前処理（仕様書 3.1）"""

    def __init__(self, target_area: Dict[str, Any], cloud_threshold: int = 20):
        self.target_area = target_area
        self.cloud_threshold = cloud_threshold

    def atmospheric_correction(self, image: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        """大気補正（L2A 使用時は適用済み）"""
        return image

    def cloud_masking(
        self, image: Dict[str, np.ndarray], scl_band: Optional[np.ndarray] = None
    ) -> Dict[str, np.ndarray]:
        """
        雲マスク処理。SCL バンドがあれば雲・シャドウをマスクして適用。
        scl_band が None の場合は画像をそのまま返す。
        """
        if scl_band is None:
            return image
        # SCL: 3=cloud shadows, 8=cloud medium prob, 9=cloud high prob, 10=thin cirrus
        cloud_values = (3, 8, 9, 10)
        cloud_mask = np.isin(scl_band, cloud_values)
        out = {}
        for k, v in image.items():
            if isinstance(v, np.ndarray) and v.shape == scl_band.shape:
                masked = np.where(cloud_mask, np.nan, v)
                out[k] = masked.astype(np.float64)
            else:
                out[k] = v
        return out

    def calculate_indices(self, image: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        """
        植生指数の計算（仕様書 3.1）.
        image: RED, GREEN, BLUE, NIR をキーに持つ配列（0-1 または反射率）。
        """
        red = np.asarray(image["RED"], dtype=np.float64)
        green = np.asarray(image["GREEN"], dtype=np.float64)
        blue = np.asarray(image["BLUE"], dtype=np.float64)
        nir = np.asarray(image["NIR"], dtype=np.float64)

        # ゼロ除算を避ける
        eps = 1e-10
        ndvi = (nir - red) / (nir + red + eps)
        evi = 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1 + eps)
        ndwi = (green - nir) / (green + nir + eps)
        savi = 1.5 * (nir - red) / (nir + red + 0.5 + eps)

        return {
            "NDVI": ndvi,
            "EVI": evi,
            "NDWI": ndwi,
            "SAVI": savi,
        }


def create_mock_image(rows: int = 100, cols: int = 100) -> Dict[str, np.ndarray]:
    """
    テスト/モック用の擬似 Sentinel バンド画像を生成。
    森林地帯を模した NDVI 高めの値にする。
    """
    np.random.seed(42)
    red = np.clip(np.random.rand(rows, cols) * 0.2 + 0.1, 0, 1)
    green = np.clip(red * 1.2 + np.random.rand(rows, cols) * 0.1, 0, 1)
    blue = np.clip(red * 0.9 + np.random.rand(rows, cols) * 0.05, 0, 1)
    nir = np.clip(red * 2.5 + np.random.rand(rows, cols) * 0.2, 0, 1)
    return {"RED": red, "GREEN": green, "BLUE": blue, "NIR": nir}


def create_mock_image_after_deforestation(
    before_bands: Dict[str, np.ndarray],
    deforest_bbox: Tuple[int, int, int, int],
) -> Dict[str, np.ndarray]:
    """
    開発後画像を模擬: 指定矩形内で NIR を下げ RED を上げ（伐採・土露出）。
    deforest_bbox: (r0, c0, r1, c1)
    """
    after = {k: v.copy() for k, v in before_bands.items()}
    r0, c0, r1, c1 = deforest_bbox
    after["NIR"][r0:r1, c0:c1] = np.clip(after["NIR"][r0:r1, c0:c1] * 0.4 + 0.1, 0, 1)
    after["RED"][r0:r1, c0:c1] = np.clip(after["RED"][r0:r1, c0:c1] * 1.5 + 0.15, 0, 1)
    after["GREEN"][r0:r1, c0:c1] = np.clip(after["GREEN"][r0:r1, c0:c1] * 0.9, 0, 1)
    after["BLUE"][r0:r1, c0:c1] = np.clip(after["BLUE"][r0:r1, c0:c1] * 0.9, 0, 1)
    return after
