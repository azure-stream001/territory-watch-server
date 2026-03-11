"""
Copernicus Data Space Ecosystem: OAuth2 + OData search and download.

Supports two auth methods (use one or both in .env):

1. **Username + password** (recommended for download): CDSE_USERNAME, CDSE_PASSWORD.
   Uses client_id=cdse-public and grant_type=password. The resulting token has the
   correct audience for the download service (avoids DAT-ZIP-609 "Token audience not allowed").

2. **OAuth client** (machine-to-machine): CDSE_CLIENT_ID, CDSE_CLIENT_SECRET.
   Uses client_credentials. Works for catalogue search; download may return 401
   DAT-ZIP-609 if the token audience is not accepted by the download host.

When both are set, username/password is preferred so that catalogue and download both work.
Ref: https://documentation.dataspace.copernicus.eu/APIs/Token.html
     https://documentation.dataspace.copernicus.eu/APIs/OData.html
"""
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CATALOGUE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
DOWNLOAD_URL_BASE = "https://download.dataspace.copernicus.eu/odata/v1"

# Public client used by CDSE for password grant (token has download audience).
CDSE_PUBLIC_CLIENT_ID = "cdse-public"

# In-memory token cache (token_string, expires_at).
_token_cache: Optional[Tuple[str, float]] = None


def get_client_credentials() -> Optional[Tuple[str, str]]:
    """Return (client_id, client_secret) from env if both set."""
    cid = os.environ.get("CDSE_CLIENT_ID") or os.environ.get("COPERNICUS_CLIENT_ID")
    secret = os.environ.get("CDSE_CLIENT_SECRET") or os.environ.get("COPERNICUS_CLIENT_SECRET")
    if cid and secret:
        return (cid.strip(), secret.strip())
    return None


def get_password_credentials() -> Optional[Tuple[str, str]]:
    """Return (username, password) from env if both set. Used with cdse-public for download."""
    user = os.environ.get("CDSE_USERNAME") or os.environ.get("COPERNICUS_USERNAME")
    pwd = os.environ.get("CDSE_PASSWORD") or os.environ.get("COPERNICUS_PASSWORD")
    if user and pwd:
        return (user.strip(), pwd.strip())
    return None


def has_dataspace_credentials() -> bool:
    """True if either OAuth client or username/password is set (for catalogue and download)."""
    return get_client_credentials() is not None or get_password_credentials() is not None


def get_access_token() -> Optional[str]:
    """
    Obtain OAuth2 access token. Cached until near expiry.

    Prefers username+password with cdse-public when CDSE_USERNAME/CDSE_PASSWORD are set
    (token has correct audience for download; avoids DAT-ZIP-609). Otherwise uses
    client_credentials with CDSE_CLIENT_ID/SECRET.
    """
    global _token_cache
    now = time.time()
    if _token_cache is not None and _token_cache[1] > now + 60:
        return _token_cache[0]

    # Prefer password grant so download works (audience allowed by download host).
    password_creds = get_password_credentials()
    if password_creds:
        username, password = password_creds
        try:
            r = requests.post(
                TOKEN_URL,
                data={
                    "client_id": CDSE_PUBLIC_CLIENT_ID,
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            token = data.get("access_token")
            if token:
                expires_in = data.get("expires_in", 300)
                _token_cache = (token, now + expires_in)
                return token
        except Exception:
            _token_cache = None
            return None

    # Client credentials (catalogue works; download may return DAT-ZIP-609).
    creds = get_client_credentials()
    if not creds:
        return None
    client_id, client_secret = creds
    try:
        r = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
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


def wkt_to_odata_geography(wkt: str) -> str:
    """Convert WKT POLYGON to OData geography literal (SRID=4326;POLYGON(...))."""
    wkt = wkt.strip()
    # Accept POLYGON((...)) or POLYGON ((...))
    m = re.match(r"POLYGON\s*\(\s*\((.+)\)\s*\)", wkt, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    inner = m.group(1).strip()
    return f"SRID=4326;POLYGON(({inner}))"


def query_products_dataspace(
    footprint_wkt: str,
    date_start: str,
    date_end: str,
    cloud_max: float = 50.0,
) -> List[Dict[str, Any]]:
    """
    Search Sentinel-2 L2A products in Data Space by footprint and date.
    date_start/date_end: YYYYMMDD.
    Returns list of product dicts with at least 'Id', 'Name'.
    """
    token = get_access_token()
    if not token:
        return []
    geo = wkt_to_odata_geography(footprint_wkt)
    if not geo:
        return []
    # OData filter: SENTINEL-2, L2A (Name contains MSIL2A), intersects, date, cloud
    # ContentDate format in OData: 2019-01-15T00:00:00.000Z
    from_start = f"{date_start[:4]}-{date_start[4:6]}-{date_start[6:8]}T00:00:00.000Z"
    to_end = f"{date_end[:4]}-{date_end[4:6]}-{date_end[6:8]}T23:59:59.000Z"
    # Geography in filter must be single-quoted and URL-encoded
    geo_escaped = geo.replace("'", "''")  # OData escape single quote by doubling
    base_filter_parts = [
        "Collection/Name eq 'SENTINEL-2'",
        f"startswith(Name,'S2') and contains(Name,'MSIL2A')",
        f"OData.CSC.Intersects(area=geography'{geo_escaped}')",
        f"ContentDate/Start gt {from_start}",
        f"ContentDate/Start lt {to_end}",
    ]
    for filter_parts in [
        base_filter_parts + [f"Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le {cloud_max})"],
        base_filter_parts,
    ]:
        filter_str = " and ".join(filter_parts)
        params = {"$filter": filter_str, "$top": 50, "$orderby": "ContentDate/Start desc"}
        try:
            r = requests.get(
                f"{CATALOGUE_URL}/Products",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            values = data.get("value") or []
            if values:
                return values
        except Exception:
            pass
    return []


def download_product_dataspace(
    product_id: str, save_path: str
) -> Tuple[bool, str]:
    """
    Download product by UUID to save_path.
    product_id: UUID from OData search (e.g. product["Id"]).
    Returns (success, error_detail). error_detail is empty on success.

    CDSE redirects to a download host; requests does not forward Authorization
    to other hosts. We follow redirects manually and send the token on each request
    to avoid 401 "Token not found" (DAT-ZIP-604).
    """
    token = get_access_token()
    if not token:
        return False, "No access token. Set CDSE_USERNAME and CDSE_PASSWORD (recommended for download), or CDSE_CLIENT_ID and CDSE_CLIENT_SECRET."
    url = f"{CATALOGUE_URL}/Products({product_id})/$value"
    headers = {"Authorization": f"Bearer {token}"}
    max_redirects = 10
    try:
        while max_redirects > 0:
            r = requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=600,
                allow_redirects=False,
            )
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("Location")
                if not location:
                    return False, "Redirect response missing Location header."
                url = location if location.startswith("http") else urljoin(url, location)
                max_redirects -= 1
                continue
            if not r.ok:
                body = (r.text or "")[:500]
                return False, f"HTTP {r.status_code} {r.reason}. {body}"
            break
        if max_redirects <= 0:
            return False, "Too many redirects."
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True, ""
    except requests.exceptions.Timeout:
        return False, "Download timed out (600s)."
    except requests.exceptions.RequestException as e:
        return False, str(e)[:400]
    except OSError as e:
        return False, f"Write error: {e!s}"[:400]
