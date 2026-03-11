"""
仕様書 Phase 3 精度評価.
Precision, Recall, F1, IoU, Pixel Accuracy。
"""
from typing import Any, Dict, Optional

import numpy as np


class AccuracyAssessment:
    """検出精度の評価（仕様書 Phase 3）"""

    def __init__(self, ground_truth: Optional[np.ndarray] = None):
        """
        ground_truth: 二値マスク (1=正解の伐採域)。None の場合はスコア算出をスキップ。
        """
        self.ground_truth = ground_truth

    def evaluate(
        self,
        detection_binary: np.ndarray,
        detected_area_ha: float = 0,
        actual_area_ha: float = 105,
    ) -> Dict[str, Any]:
        """精度指標の計算（仕様書 Phase 3）"""
        if self.ground_truth is None:
            return {
                "precision": None,
                "recall": None,
                "f1_score": None,
                "iou": None,
                "pixel_accuracy": None,
                "detected_area_ha": detected_area_ha,
                "actual_area_ha": actual_area_ha,
                "detection_rate": detected_area_ha / actual_area_ha if actual_area_ha else None,
            }
        gt = (self.ground_truth.astype(np.float64) > 0.5).flatten()
        pred = (detection_binary.astype(np.float64) > 0.5).flatten()
        min_len = min(len(gt), len(pred))
        gt, pred = gt[:min_len], pred[:min_len]

        tp = np.sum(gt & pred)
        fp = np.sum((~gt) & pred)
        fn = np.sum(gt & (~pred))
        tn = np.sum((~gt) & (~pred))

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        iou = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0
        pixel_accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) > 0 else 0.0

        detection_rate = detected_area_ha / actual_area_ha if actual_area_ha else None

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "iou": round(iou, 4),
            "pixel_accuracy": round(pixel_accuracy, 4),
            "detected_area_ha": detected_area_ha,
            "actual_area_ha": actual_area_ha,
            "detection_rate": round(detection_rate, 4) if detection_rate is not None else None,
        }

    def generate_report(self, metrics: Dict[str, Any], area_name: str = "静岡県伊東市八幡野地区") -> str:
        """評価レポートの生成（仕様書 Phase 3）"""
        lines = [
            "=== 検出精度評価レポート ===",
            "",
            f"対象地域: {area_name}",
            "検証期間: 2017-2019",
            "",
            "【精度指標】",
        ]
        for k in ("precision", "recall", "f1_score", "iou", "pixel_accuracy"):
            v = metrics.get(k)
            if v is not None and isinstance(v, (int, float)):
                lines.append(f"- {k}: {v:.2%}")
            elif v is not None:
                lines.append(f"- {k}: {v}")
        lines.extend([
            "",
            "【検出結果】",
            f"- 森林伐採面積: {metrics.get('detected_area_ha', '—')} ha",
            f"- 実際の伐採面積: {metrics.get('actual_area_ha', 105)} ha",
        ])
        dr = metrics.get("detection_rate")
        if dr is not None:
            lines.append(f"- 検出率: {dr:.1%}")
        lines.append("")
        return "\n".join(lines)
