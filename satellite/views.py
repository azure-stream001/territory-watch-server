import io
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import SatelliteScene
from .scene_preview import get_or_create_scene_preview, scene_has_tiles
from .serializers import SatelliteSceneSerializer, SatelliteSceneCreateSerializer
from .services import fetch_and_save_scene
from .sentinelhub_processing import (
    get_client_credentials,
    wkt_to_bbox,
    get_ndvi_change,
)


class SatelliteSceneViewSet(viewsets.ModelViewSet):
    queryset = SatelliteScene.objects.select_related("area").all()
    serializer_class = SatelliteSceneSerializer
    permission_classes = [AllowAny]
    filterset_fields = ["area", "status", "scene_date"]

    def get_serializer_class(self):
        if self.action == "create":
            return SatelliteSceneCreateSerializer
        return SatelliteSceneSerializer

    def perform_destroy(self, instance):
        """Delete files explicitly before removing the DB row."""
        import shutil
        from pathlib import Path

        scene_id = instance.id
        file_path = (instance.file_path or "").strip()

        # Remove preview/tiles directory
        preview_dir = Path(settings.MEDIA_ROOT) / "scene_previews" / str(scene_id)
        if preview_dir.exists():
            shutil.rmtree(preview_dir, ignore_errors=True)

        # Remove product file if no other scene shares it
        if file_path:
            p = Path(file_path) if Path(file_path).is_absolute() else Path(settings.MEDIA_ROOT) / file_path
            if p.exists():
                try:
                    media_root = Path(settings.MEDIA_ROOT).resolve()
                    if str(p.resolve()).startswith(str(media_root)):
                        others = SatelliteScene.objects.filter(file_path=file_path).exclude(pk=scene_id).exists()
                        if not others:
                            if p.is_file():
                                p.unlink()
                            elif p.is_dir():
                                shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass

        instance.delete()

    def create(self, request, *args, **kwargs):
        ser = SatelliteSceneCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        area = ser.validated_data["area"]
        scene_date = ser.validated_data["scene_date"]
        max_cloud = ser.validated_data.get("max_cloud_coverage", 50)
        cloud_cover = (0, max(0, min(100, max_cloud)))
        scene, _ = fetch_and_save_scene(area, scene_date, cloud_cover=cloud_cover)
        data = SatelliteSceneSerializer(scene).data
        # 取得失敗時は 422 を返してクライアントがエラーと分かるようにする
        if scene.status == SatelliteScene.Status.FAILED:
            return Response(data, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        """
        Return preview image URL, bounds, and optional tile layer URL for the scene's real imagery.
        When tiles exist, the frontend should use the tile layer for correct imagery on zoom/pan.
        On generation failure (e.g. missing zip, unsupported product) returns 200 with nulls so
        the detection detail page can still show the map with footprint only.
        """
        scene = self.get_object()
        try:
            rel_path, bounds = get_or_create_scene_preview(scene.id)
        except Exception:
            return Response({"image_url": None, "bounds": None, "tiles_url": None})
        if not rel_path:
            return Response({"image_url": None, "bounds": None, "tiles_url": None})
        media_prefix = (settings.MEDIA_URL or "media/").strip("/")
        prefix = f"/{media_prefix}" if media_prefix else ""
        image_url = f"{prefix}/{rel_path}"
        tiles_url = None
        if scene_has_tiles(scene.id):
            tiles_url = f"{prefix}/scene_previews/{scene.id}/tiles/{{z}}/{{x}}/{{y}}.png"
        return Response({"image_url": image_url, "bounds": bounds, "tiles_url": tiles_url})


@api_view(["POST"])
@permission_classes([AllowAny])
def ndvi_change_view(request):
    """
    Forest logging detection via Sentinel Hub Processing API (two-date NDVI change).

    Requires SENTINELHUB_CLIENT_ID and SENTINELHUB_CLIENT_SECRET in env.

    Body (JSON):
      - bbox: [min_lon, min_lat, max_lon, max_lat] OR footprint_wkt: WKT POLYGON
      - date_old_from, date_old_to: "YYYY-MM-DD" (before period)
      - date_recent_from, date_recent_to: "YYYY-MM-DD" (after period)
      - Optional: threshold_ndvi_drop (-0.3), forest_ndvi_min (0.6), max_cloud_coverage (20)

    Returns: ndvi_before, ndvi_after, ndvi_change, likely_logging, or error.
    """
    if not get_client_credentials():
        return Response(
            {"error": "Sentinel Hub credentials not set (SENTINELHUB_CLIENT_ID, SENTINELHUB_CLIENT_SECRET)."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    data = request.data or {}
    bbox = data.get("bbox")
    if not bbox and data.get("footprint_wkt"):
        bbox = wkt_to_bbox(data["footprint_wkt"])
    if not bbox or len(bbox) != 4:
        return Response(
            {"error": "Provide bbox [min_lon, min_lat, max_lon, max_lat] or footprint_wkt (WKT POLYGON)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    date_old_from = data.get("date_old_from") or data.get("date_before")
    date_old_to = data.get("date_old_to") or date_old_from
    date_recent_from = data.get("date_recent_from") or data.get("date_after")
    date_recent_to = data.get("date_recent_to") or date_recent_from
    if not date_old_from or not date_recent_from:
        return Response(
            {"error": "Provide date_old_from/date_old_to and date_recent_from/date_recent_to (YYYY-MM-DD)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    result = get_ndvi_change(
        bbox=bbox,
        date_old_from=date_old_from,
        date_old_to=date_old_to,
        date_recent_from=date_recent_from,
        date_recent_to=date_recent_to,
        max_cloud_coverage=int(data.get("max_cloud_coverage", 20)),
        threshold_ndvi_drop=float(data.get("threshold_ndvi_drop", -0.3)),
        forest_ndvi_min=float(data.get("forest_ndvi_min", 0.6)),
    )
    if "error" in result:
        return Response(result, status=status.HTTP_502_BAD_GATEWAY)
    return Response(result, status=status.HTTP_200_OK)


def scene_preview_tile_serve(request, scene_id: int, subpath: str):
    """
    Serve a scene preview tile. If the file exists, return it; otherwise return
    a 256x256 transparent PNG so the map never 404s for missing tiles (e.g. old cache).
    """
    tile_path = Path(settings.MEDIA_ROOT) / "scene_previews" / str(scene_id) / "tiles" / subpath
    if tile_path.is_file():
        return FileResponse(open(tile_path, "rb"), content_type="image/png")
    try:
        from PIL import Image
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return HttpResponse(buf.getvalue(), content_type="image/png")
    except ImportError:
        return HttpResponse(b"", content_type="image/png", status=404)
