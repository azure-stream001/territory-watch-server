import io
import datetime
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

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


class QuickDetectView(APIView):
    """
    POST /api/quick-detect/

    統合型クイック検出: エリアポリゴン + Before/After 年を受け取り
    Sentinel-2 シーンを取得して3パネル比較画像を生成・返す。
    前処理・検出・可視化をすべてバックエンドで完結し、画像URLと統計値を返す。

    Request body (JSON):
      footprint_wkt     : WKT POLYGON (必須)
      center_lat        : float
      center_lon        : float
      before_year       : int  (default 2018)
      after_year        : int  (default current year)
      veg_threshold     : float (default 0.3)  NDVI > this → vegetation
      ndvi_threshold    : float (default -0.3) change detection drop threshold
      max_cloud_coverage: int   (default 30)
      panel_size        : int   (default 512)  each panel's width/height in px
    """

    permission_classes = [AllowAny]

    def post(self, request):  # noqa: C901
        data = request.data or {}

        footprint_wkt = (data.get("footprint_wkt") or "").strip()
        if not footprint_wkt:
            return Response({"status": "error", "message": "footprint_wkt は必須です"}, status=400)

        try:
            center_lat = float(data.get("center_lat") or 34.9656)
            center_lon = float(data.get("center_lon") or 139.1147)
        except (TypeError, ValueError):
            center_lat, center_lon = 34.9656, 139.1147

        def _parse_iso_date(v) -> "datetime.date | None":
            if not v:
                return None
            if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
                return v
            try:
                return datetime.date.fromisoformat(str(v))
            except Exception:
                return None

        before_date = _parse_iso_date(data.get("before_date"))
        after_date = _parse_iso_date(data.get("after_date"))

        current_year = datetime.date.today().year
        try:
            before_year = int(data.get("before_year") or 2018)
            after_year  = int(data.get("after_year")  or current_year)
        except (TypeError, ValueError):
            before_year, after_year = 2018, current_year

        try:
            veg_threshold  = float(data.get("veg_threshold")  or 0.3)
            max_cloud      = int(data.get("max_cloud_coverage") or 30)
            panel_size     = int(data.get("panel_size") or 512)
        except (TypeError, ValueError):
            veg_threshold, max_cloud, panel_size = 0.3, 30, 512

        # ── 1. Create a persistent Area so SatelliteScene FK is satisfied ─────
        from areas.models import Area
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        area = Area.objects.create(
            name=f"クイック検出 {ts}",
            footprint_wkt=footprint_wkt,
            center_lat=center_lat,
            center_lon=center_lon,
        )

        # ── 2. Fetch Before scene ─────────────────────────────────────────────
        from .services import fetch_scene_in_window
        cloud_cover = (0, max(0, min(100, max_cloud)))

        # If explicit dates are provided, search within a small window around them.
        # Otherwise, fall back to a full-year search (legacy behaviour).
        try:
            window_days = int(data.get("window_days") or 45)
        except (TypeError, ValueError):
            window_days = 45

        if before_date:
            before_start = before_date - datetime.timedelta(days=window_days)
            before_end   = before_date + datetime.timedelta(days=window_days)
        else:
            before_start = datetime.date(before_year, 1, 1)
            before_end   = datetime.date(before_year, 12, 31)

        if after_date:
            after_start = after_date - datetime.timedelta(days=window_days)
            after_end   = after_date + datetime.timedelta(days=window_days)
        else:
            after_start  = datetime.date(after_year,  1, 1)
            after_end    = datetime.date(after_year,  12, 31)

        try:
            scene_before = fetch_scene_in_window(
                area, before_start, before_end,
                cloud_cover=cloud_cover, prefer_earliest=True,
            )
        except Exception as e:
            area.delete()
            return Response(
                {"status": "error", "message": f"Before シーン取得中にエラー: {e}"},
                status=200,
            )

        if not scene_before or scene_before.status != "downloaded":
            area.delete()
            msg = getattr(scene_before, "error_message", "") or "取得失敗"
            return Response(
                {"status": "failed", "message": f"Before シーン（{before_year}年）を取得できませんでした。{msg}"},
                status=200,
            )

        # ── 3. Fetch After scene ──────────────────────────────────────────────
        try:
            scene_after = fetch_scene_in_window(
                area, after_start, after_end,
                cloud_cover=cloud_cover, prefer_earliest=False,
            )
        except Exception as e:
            area.delete()
            return Response(
                {"status": "error", "message": f"After シーン取得中にエラー: {e}"},
                status=200,
            )

        if not scene_after or scene_after.status != "downloaded":
            area.delete()
            msg = getattr(scene_after, "error_message", "") or "取得失敗"
            return Response(
                {"status": "failed", "message": f"After シーン（{after_year}年）を取得できませんでした。{msg}"},
                status=200,
            )

        # Guard: same scene selected for both → no meaningful comparison
        if scene_before.id == scene_after.id:
            area.delete()
            return Response(
                {"status": "failed", "message": "Before と After に同じシーンが選ばれました。年の範囲を広げてください。"},
                status=200,
            )

        # ── 4. Generate 3-panel comparison image ─────────────────────────────
        from .quick_detect import generate_quick_detect_image
        try:
            rel_path, img_stats = generate_quick_detect_image(
                before_scene=scene_before,
                after_scene=scene_after,
                area=area,
                veg_threshold=veg_threshold,
                panel_size=panel_size,
                before_year=before_year,
                after_year=after_year,
                before_label=(str(before_date) if before_date else ""),
                after_label=(str(after_date) if after_date else ""),
            )
        except Exception as e:
            area.delete()
            return Response({"status": "error", "message": f"画像生成エラー: {e}"}, status=200)

        if rel_path is None:
            area.delete()
            err = img_stats.get("error", "画像生成に失敗しました")
            return Response({"status": "failed", "message": err}, status=200)

        # ── 5. Build image URL ────────────────────────────────────────────────
        media_prefix = (settings.MEDIA_URL or "media/").strip("/")
        prefix = f"/{media_prefix}" if media_prefix else ""
        image_url = f"{prefix}/{rel_path}"

        return Response({
            "status": "success",
            "image_url": image_url,
            "before_scene_date": str(scene_before.scene_date),
            "after_scene_date":  str(scene_after.scene_date),
            "area_id": area.id,
            **img_stats,
        })


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
