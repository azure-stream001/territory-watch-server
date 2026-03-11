"""
Sentinel Hub Processing API client for on-the-fly NDVI and forest logging detection.

Use when SENTINELHUB_CLIENT_ID and SENTINELHUB_CLIENT_SECRET are set (from Sentinel Hub
OAuth client). Do NOT call Sentinel Hub from the frontend — keep credentials on the backend.

Ref: https://docs.sentinel-hub.com/api/latest/reference/
     POST https://services.sentinel-hub.com/api/v1/process
"""
import io
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

TOKEN_URL = "https://services.sentinel-hub.com/oauth/token"
PROCESS_URL = "https://services.sentinel-hub.com/api/v1/process"

# In-memory token cache (token_string, expires_at)
_token_cache: Optional[Tuple[str, float]] = None

# Evalscript: Sentinel-2 L2A B04 (Red), B08 (NIR) → single-band NDVI
# NDVI = (NIR - RED) / (NIR + RED); output in [0, 1] scale for compatibility
NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08"],
    output: { bands: 1 }
  };
}
function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);
  // Clamp to [0,1] for single-band image; no-data handled as 0
  ndvi = isFinite(ndvi) ? Math.max(0, Math.min(1, (ndvi + 1) / 2)) : 0;
  return [ndvi];
}
"""


def get_client_credentials() -> Optional[Tuple[str, str]]:
    """Return (client_id, client_secret) from env if both set."""
    cid = os.environ.get("SENTINELHUB_CLIENT_ID") or os.environ.get("SH_CLIENT_ID")
    secret = os.environ.get("SENTINELHUB_CLIENT_SECRET") or os.environ.get("SH_CLIENT_SECRET")
    if cid and secret:
        return (cid.strip(), secret.strip())
    return None


def get_access_token() -> Optional[str]:
    """Obtain OAuth2 access token (client_credentials). Cached until near expiry."""
    global _token_cache
    creds = get_client_credentials()
    if not creds:
        return None
    client_id, client_secret = creds
    now = time.time()
    if _token_cache is not None and _token_cache[1] > now + 60:
        return _token_cache[0]
    try:
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("access_token")
        if not token:
            return None
        expires_in = data.get("expires_in", 300)
        _token_cache = (token, now + expires_in)
        return token
    except Exception:
        _token_cache = None
        return None


def wkt_to_bbox(wkt_string: str) -> Optional[List[float]]:
    """
    Convert WKT POLYGON (or MULTIPOLYGON) to Sentinel Hub bbox [min_lon, min_lat, max_lon, max_lat].
    Returns None if parse fails or geometry is empty.
    """
    if not (wkt_string or "").strip():
        return None
    try:
        from shapely import wkt as wkt_module
        geom = wkt_module.loads(wkt_string.strip())
    except Exception:
        return None
    if geom is None or geom.is_empty:
        return None
    minx, miny, maxx, maxy = geom.bounds
    return [minx, miny, maxx, maxy]


def _build_process_payload(
    bbox: List[float],
    date_from: str,
    date_to: str,
    evalscript: str,
    width: int = 512,
    height: int = 512,
    max_cloud_coverage: int = 20,
    output_format: str = "image/tiff",
) -> Dict[str, Any]:
    """Build request body for Process API (bounds, time range, evalscript, output)."""
    return {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{date_from}T00:00:00Z",
                            "to": f"{date_to}T23:59:59Z",
                        },
                        "maxCloudCoverage": max_cloud_coverage,
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {
                    "identifier": "default",
                    "format": {"type": output_format},
                }
            ],
        },
        "evalscript": evalscript,
    }


def request_ndvi_image(
    bbox: List[float],
    date_from: str,
    date_to: str,
    width: int = 512,
    height: int = 512,
    max_cloud_coverage: int = 20,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Request NDVI image from Sentinel Hub Processing API.

    bbox: [min_lon, min_lat, max_lon, max_lat] (WGS84)
    date_from / date_to: "YYYY-MM-DD"
    Returns (tiff_bytes, error_message). error_message is None on success.
    """
    token = get_access_token()
    if not token:
        return None, "Sentinel Hub credentials not set (SENTINELHUB_CLIENT_ID, SENTINELHUB_CLIENT_SECRET)."

    payload = _build_process_payload(
        bbox=bbox,
        date_from=date_from,
        date_to=date_to,
        evalscript=NDVI_EVALSCRIPT,
        width=width,
        height=height,
        max_cloud_coverage=max_cloud_coverage,
        output_format="image/tiff",
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            PROCESS_URL,
            headers=headers,
            data=json.dumps(payload),
            timeout=120,
        )
        if not r.ok:
            return None, f"HTTP {r.status_code}: {(r.text or '')[:500]}"
        return r.content, None
    except requests.exceptions.Timeout:
        return None, "Request timed out (120s)."
    except requests.exceptions.RequestException as e:
        return None, str(e)[:400]


def request_ndvi_array(
    bbox: List[float],
    date_from: str,
    date_to: str,
    width: int = 512,
    height: int = 512,
    max_cloud_coverage: int = 20,
) -> Tuple[Optional[Any], Optional[str]]:
    """
    Request NDVI from Processing API and decode to numpy array (float, shape (H,W)).
    Values are in 0–1 (evalscript maps NDVI [-1,1] to [0,1] for image). For change detection
    you may want to map back: ndvi_real = arr * 2 - 1.

    Returns (ndvi_array, error_message). error_message is None on success.
    """
    tiff_bytes, err = request_ndvi_image(
        bbox=bbox,
        date_from=date_from,
        date_to=date_to,
        width=width,
        height=height,
        max_cloud_coverage=max_cloud_coverage,
    )
    if err or tiff_bytes is None:
        return None, err or "No image returned."

    try:
        import numpy as np
        import rasterio
        with rasterio.open(io.BytesIO(tiff_bytes)) as src:
            arr = src.read(1)
        return np.asarray(arr, dtype=np.float64), None
    except Exception as e:
        return None, f"Decode error: {e!s}"[:400]


def get_ndvi_change(
    bbox: List[float],
    date_old_from: str,
    date_old_to: str,
    date_recent_from: str,
    date_recent_to: str,
    width: int = 512,
    height: int = 512,
    max_cloud_coverage: int = 20,
    threshold_ndvi_drop: float = -0.3,
    forest_ndvi_min: float = 0.6,
) -> Dict[str, Any]:
    """
    Two-date NDVI change for forest logging detection.

    Fetches NDVI for "old" and "recent" windows, computes mean NDVI and flags
    likely logging if there is a large negative drop and the area was previously forest-like.

    bbox: [min_lon, min_lat, max_lon, max_lat]
    date_*: "YYYY-MM-DD"

    Returns dict with:
      - ndvi_before, ndvi_after: mean NDVI (in 0–1 scale from API)
      - ndvi_change: after - before (negative = vegetation loss)
      - likely_logging: True if drop <= threshold_ndvi_drop and ndvi_before >= forest_ndvi_min
      - error: present only on failure
    """
    import numpy as np

    before_arr, err_before = request_ndvi_array(
        bbox=bbox,
        date_from=date_old_from,
        date_to=date_old_to,
        width=width,
        height=height,
        max_cloud_coverage=max_cloud_coverage,
    )
    if err_before:
        return {"error": f"Before period: {err_before}"}

    after_arr, err_after = request_ndvi_array(
        bbox=bbox,
        date_from=date_recent_from,
        date_to=date_recent_to,
        width=width,
        height=height,
        max_cloud_coverage=max_cloud_coverage,
    )
    if err_after:
        return {"error": f"After period: {err_after}"}

    # Evalscript outputs NDVI in [0,1] (mapped from [-1,1]); convert back for thresholds
    valid_before = np.isfinite(before_arr) & (before_arr > 0)
    valid_after = np.isfinite(after_arr) & (after_arr > 0)
    mask = valid_before & valid_after
    if not np.any(mask):
        return {
            "ndvi_before": None,
            "ndvi_after": None,
            "ndvi_change": None,
            "likely_logging": False,
            "message": "No valid pixels in overlap.",
        }

    # Scale [0,1] back to real NDVI [-1, 1]: real = arr*2 - 1
    ndvi_before = float(np.mean(before_arr[mask]) * 2.0 - 1.0)
    ndvi_after = float(np.mean(after_arr[mask]) * 2.0 - 1.0)
    ndvi_change = ndvi_after - ndvi_before

    likely_logging = (
        ndvi_change <= threshold_ndvi_drop
        and ndvi_before >= forest_ndvi_min
    )

    return {
        "ndvi_before": round(ndvi_before, 4),
        "ndvi_after": round(ndvi_after, 4),
        "ndvi_change": round(ndvi_change, 4),
        "likely_logging": likely_logging,
        "threshold_ndvi_drop": threshold_ndvi_drop,
        "forest_ndvi_min": forest_ndvi_min,
    }
