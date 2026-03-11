"""
Sentinel-2 データ取得（仕様書 Phase 1, 2.2）.
Copernicus Data Space (OAuth2) を優先。未設定時は旧 SciHub (DHUS) にフォールバック（終了済みのため新規は Data Space 必須）。
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from sentinelsat import SentinelAPI
    SENTINELSAT_AVAILABLE = True
except ImportError:
    SENTINELSAT_AVAILABLE = False


def _dataspace_available() -> bool:
    from .dataspace_client import has_dataspace_credentials
    return has_dataspace_credentials()


def get_api() -> Optional[Any]:
    """環境変数から Copernicus 認証情報を取得し API を返す（旧 DHUS 用）。未設定なら None。"""
    if not SENTINELSAT_AVAILABLE:
        return None
    user = os.environ.get("SENTINEL_USER") or os.environ.get("COPERNICUS_USER")
    password = os.environ.get("SENTINEL_PASSWORD") or os.environ.get("COPERNICUS_PASSWORD")
    if not user or not password:
        return None
    return SentinelAPI(user, password, "https://scihub.copernicus.eu/dhus")


def query_products(
    footprint_wkt: str,
    date_start: str,
    date_end: str,
    cloud_cover: Tuple[int, int] = (0, 20),
) -> List[Any]:
    """
    指定範囲・期間で Sentinel-2 製品を検索する。
    Data Space 認証があれば OData を使用、なければ旧 DHUS（sentinelsat）。
    """
    if _dataspace_available():
        from .dataspace_client import query_products_dataspace
        cloud_max = float(cloud_cover[1]) if cloud_cover else 50.0
        products = query_products_dataspace(
            footprint_wkt, date_start, date_end, cloud_max=cloud_max
        )
        # Return list of (Id, info_dict) to match legacy format where code uses products[0][0]
        return [(p["Id"], p) for p in products] if products else []
    api = get_api()
    if api is None:
        return []
    try:
        products = api.query(
            area=footprint_wkt,
            date=(date_start, date_end),
            platformname="Sentinel-2",
            cloudcoverpercentage=cloud_cover,
            producttype="S2MSI2A",
        )
        return list(products.items())
    except Exception:
        return []


def download_sentinel2(
    footprint_wkt: str,
    date_range: Tuple[str, str],
    cloud_cover: Tuple[int, int] = (0, 20),
    download_dir: Optional[str] = None,
    prefer_earliest: bool = False,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Sentinel-2 L2A を検索し、1シーンをダウンロードする。
    prefer_earliest=True: 期間内で最も早い日付の製品を使用（Before 用）。
                  False: 最も新しい日付（After 用、デフォルト）。
    戻り値: (path, failure_reason, detail)。成功時 (path, None, None)。失敗時 (None, reason, detail)。
    """
    if _dataspace_available():
        from .dataspace_client import (
            download_product_dataspace,
            query_products_dataspace,
        )
        cloud_max = float(cloud_cover[1]) if cloud_cover else 50.0
        products = query_products_dataspace(
            footprint_wkt,
            date_range[0],
            date_range[1],
            cloud_max=cloud_max,
        )
        if not products:
            return None, "no_products", None
        # products are ordered by ContentDate/Start desc; pick last for earliest
        chosen = products[-1] if prefer_earliest else products[0]
        product_id = chosen["Id"]
        name = chosen.get("Name", "product") or "product"
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        if not safe_name.endswith(".zip"):
            safe_name = safe_name + ".zip"
        base = Path(download_dir) if download_dir else Path.cwd()
        save_path = str(base / safe_name)
        ok, detail = download_product_dataspace(product_id, save_path)
        if ok:
            return save_path, None, None
        return None, "download_failed", (detail or "")
    api = get_api()
    if api is None:
        return None, "no_credentials", None
    products = query_products(
        footprint_wkt,
        date_range[0],
        date_range[1],
        cloud_cover=cloud_cover,
    )
    if not products:
        return None, "no_products", None
    product_id = products[0][0]
    try:
        if download_dir:
            path = api.download(product_id, directory_path=download_dir)
        else:
            path = api.download(product_id)
        return path, None, None
    except Exception as e:
        return None, "download_failed", str(e)[:400]
