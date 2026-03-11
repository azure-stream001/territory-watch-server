import urllib.error
import urllib.request
from django.http import HttpResponse
from django.views import View
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from .models import Area
from .serializers import AreaSerializer

GEOSHAPE_TILE_BASE = "https://geoshape.ex.nii.ac.jp/vector-adm"
TILE_TIMEOUT = 30


class GeoshapeTileProxyView(View):
    """Proxy for Geoshape vector tiles to avoid CORS in the browser."""

    def get(self, request, z: int, x: int, y: int):
        url = f"{GEOSHAPE_TILE_BASE}/{z}/{x}/{y}.pbf"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TerritoryWatch/1.0"})
            with urllib.request.urlopen(req, timeout=TILE_TIMEOUT) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            return HttpResponse(status=e.code)
        except (OSError, TimeoutError) as e:
            return HttpResponse(
                f"Upstream error: {type(e).__name__}",
                status=502,
                content_type="text/plain",
            )
        return HttpResponse(body, content_type="application/x-protobuf")


class AreaViewSet(viewsets.ModelViewSet):
    """対象地域の CRUD。開発・プロトタイプ用に認証なしでアクセス可能。"""
    queryset = Area.objects.all()
    serializer_class = AreaSerializer
    permission_classes = [AllowAny]
