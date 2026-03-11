"""
仕様書 3.3 違法性判定.
分割申請検出（50ha未満に分割）、急斜面開発検出。
"""
import math
from typing import Any, Dict, List


class IllegalityDetector:
    """違法開発の可能性を判定（仕様書 3.3）"""

    def __init__(
        self,
        area_threshold_ha: float = 49,
        combined_min_ha: float = 50,
        proximity_threshold_m: float = 100,
        slope_threshold_deg: float = 30,
    ):
        self.area_threshold = area_threshold_ha
        self.combined_min = combined_min_ha
        self.proximity_threshold = proximity_threshold_m
        self.slope_threshold = slope_threshold_deg

    def detect_split_applications(
        self,
        development_areas: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        環境アセス逃れの分割申請検出（仕様書 3.3）.
        development_areas: 各要素は size_ha, centroid_xy を持つ。
        """
        suspicious_cases = []
        for i, area1 in enumerate(development_areas):
            s1 = float(area1.get("size_ha", area1.get("size", 0)))
            c1 = area1.get("centroid_xy", area1.get("centroid", (0, 0)))
            x1, y1 = (float(c1[0]), float(c1[1])) if len(c1) >= 2 else (0.0, 0.0)
            for area2 in development_areas[i + 1:]:
                s2 = float(area2.get("size_ha", area2.get("size", 0)))
                c2 = area2.get("centroid_xy", area2.get("centroid", (0, 0)))
                x2, y2 = (float(c2[0]), float(c2[1])) if len(c2) >= 2 else (0.0, 0.0)
                distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2) * 10.0
                if distance < self.proximity_threshold:
                    if (s1 < self.area_threshold and s2 < self.area_threshold
                            and (s1 + s2) >= self.combined_min):
                        suspicious_cases.append({
                            "areas": [area1, area2],
                            "total_size_ha": round(s1 + s2, 2),
                            "violation_type": "split_application",
                            "distance_m": round(distance, 1),
                        })
        return suspicious_cases

    def check_slope_violation(
        self,
        development_area: Dict[str, Any],
        dem_data: Any,
    ) -> Dict[str, Any]:
        """急斜面への違法開発検出（仕様書 3.3）。"""
        if dem_data is None:
            return {"violation": False}
        slope_mean = float(development_area.get("slope_mean", development_area.get("average_slope", 0)))
        slope_max = float(development_area.get("slope_max", development_area.get("max_slope", 0)))
        if slope_mean > self.slope_threshold:
            return {
                "violation": True,
                "type": "steep_slope_development",
                "average_slope": round(slope_mean, 1),
                "max_slope": round(slope_max, 1),
            }
        return {"violation": False}
