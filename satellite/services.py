"""
衛星シーンの取得・保存。Sentinel 検索 → ダウンロード → SatelliteScene 保存。
"""
import datetime
from pathlib import Path
from typing import Optional, Tuple

from django.conf import settings

from areas.models import Area
from .models import SatelliteScene
from .sentinel_client import download_sentinel2


def _date_to_str(d: datetime.date) -> str:
    return d.strftime("%Y%m%d")


def fetch_and_save_scene(
    area: Area,
    scene_date: datetime.date,
    cloud_cover: Tuple[int, int] = (0, 50),
) -> Tuple[SatelliteScene, bool]:
    """
    指定地域・日付で Sentinel-2 を検索し、1シーンをダウンロードして SatelliteScene を保存する。
    既に同じ area+scene_date の DOWNLOADED があればその Scene を返す（再取得しない）。
    戻り値: (scene, created_or_updated).
    """
    existing = SatelliteScene.objects.filter(
        area=area,
        scene_date=scene_date,
        status=SatelliteScene.Status.DOWNLOADED,
    ).first()
    if existing:
        return existing, False

    scene, _ = SatelliteScene.objects.get_or_create(
        area=area,
        scene_date=scene_date,
        defaults={
            "status": SatelliteScene.Status.PENDING,
            "product_id": "",
        },
    )
    scene.status = SatelliteScene.Status.DOWNLOADING
    scene.error_message = ""
    scene.save(update_fields=["status", "error_message", "updated_at"])

    footprint = (area.footprint_wkt or "").strip()
    if not footprint:
        # 中心点から簡易 POLYGON（約 10km 四方）
        lon = area.center_lon or 139.1147
        lat = area.center_lat or 34.9656
        d = 0.05
        footprint = (
            f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))"
        )

    # 指定日またはそれより前で最も近い日のシーンを取得する（検索範囲: 指定日の 90 日前〜指定日）
    date_start = scene_date - datetime.timedelta(days=90)
    date_end = scene_date
    date_range = (_date_to_str(date_start), _date_to_str(date_end))

    download_dir = Path(settings.MEDIA_ROOT) / "sentinel"
    download_dir.mkdir(parents=True, exist_ok=True)
    download_path = str(download_dir)

    try:
        path, failure_reason, failure_detail = download_sentinel2(
            footprint_wkt=footprint,
            date_range=date_range,
            cloud_cover=cloud_cover,
            download_dir=download_path,
        )
        if path:
            try:
                rel = Path(path).relative_to(Path(settings.MEDIA_ROOT))
                scene.file_path = str(rel)
            except ValueError:
                scene.file_path = path
            scene.status = SatelliteScene.Status.DOWNLOADED
            scene.product_id = Path(path).stem[:255] if path else ""
            scene.metadata = scene.metadata or {}
            scene.error_message = ""
        else:
            scene.status = SatelliteScene.Status.FAILED
            if failure_reason == "no_credentials":
                scene.error_message = (
                    "Sentinel 認証が未設定です。.env に CDSE_USERNAME と CDSE_PASSWORD、または CDSE_CLIENT_ID と CDSE_CLIENT_SECRET を設定してください（docs/SENTINEL_DATA.md）。"
                )
            elif failure_reason == "no_products":
                scene.error_message = (
                    f"該当期間に L2A 製品が見つかりませんでした（指定日から90日前まで・雲量{cloud_cover[1]}%以下で検索）。"
                    " 雲量を上げるか、別の日付を試してください（例: 2017-06-15, 2019-06-01）。"
                )
            elif failure_reason == "download_failed":
                scene.error_message = (
                    failure_detail.strip()
                    if failure_detail
                    else (
                        "製品は見つかりましたがダウンロードに失敗しました（ネットワークまたはクォータ）。"
                        " CDSE ダッシュボードを確認して再試行してください。"
                    )
                )
            else:
                scene.error_message = (
                    "シーン取得に失敗しました。認証設定と日付を確認し、"
                    " python manage.py debug_sentinel_query <area_id> <YYYY-MM-DD> で原因を確認してください。"
                )
    except Exception as e:
        scene.status = SatelliteScene.Status.FAILED
        scene.error_message = str(e)
    scene.save(update_fields=["status", "file_path", "product_id", "metadata", "error_message", "updated_at"])
    return scene, True


def get_or_fetch_scene(
    area: Area,
    scene_date: datetime.date,
    cloud_cover: Tuple[int, int] = (0, 30),
) -> Optional[SatelliteScene]:
    """
    地域・日付に対応するシーンを返す。DOWNLOADED がなければ取得を試みる。
    取得失敗時は None でなく failed な Scene を返す。呼び出し側で status を確認すること。
    """
    scene = SatelliteScene.objects.filter(
        area=area,
        scene_date=scene_date,
    ).first()
    if scene and scene.status == SatelliteScene.Status.DOWNLOADED:
        return scene
    if not scene:
        scene, _ = fetch_and_save_scene(area, scene_date, cloud_cover)
    else:
        scene, _ = fetch_and_save_scene(area, scene_date, cloud_cover)
    return scene


def find_scene_for_period(
    area: Area,
    date_start: datetime.date,
    date_end: datetime.date,
) -> Optional[SatelliteScene]:
    """指定期間内で取得済みのシーンを1件返す（日付の新しい順）。"""
    return (
        SatelliteScene.objects.filter(
            area=area,
            scene_date__gte=date_start,
            scene_date__lte=date_end,
            status=SatelliteScene.Status.DOWNLOADED,
        )
        .order_by("-scene_date")
        .first()
    )


def fetch_scene_in_window(
    area: Area,
    date_start: datetime.date,
    date_end: datetime.date,
    cloud_cover: Tuple[int, int] = (0, 50),
    prefer_earliest: bool = False,
) -> Optional["SatelliteScene"]:
    """
    指定期間 [date_start, date_end] 内で Sentinel-2 シーンを1件取得・保存して返す。
    DB に既に DOWNLOADED なシーンがあればそれを使う。
    prefer_earliest=True のとき、複数ある場合は最も早い日付を優先（Before 用）。
    prefer_earliest=False（デフォルト）は最も新しい日付（After 用）。
    """
    # Check DB first
    qs = SatelliteScene.objects.filter(
        area=area,
        scene_date__gte=date_start,
        scene_date__lte=date_end,
        status=SatelliteScene.Status.DOWNLOADED,
    )
    order = "scene_date" if prefer_earliest else "-scene_date"
    existing = qs.order_by(order).first()
    if existing:
        return existing

    # Download from Sentinel: search the full window directly
    footprint = (area.footprint_wkt or "").strip()
    if not footprint:
        lon = area.center_lon or 139.1147
        lat = area.center_lat or 34.9656
        d = 0.05
        footprint = (
            f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))"
        )

    date_range = (_date_to_str(date_start), _date_to_str(date_end))
    download_dir = Path(settings.MEDIA_ROOT) / "sentinel"
    download_dir.mkdir(parents=True, exist_ok=True)
    download_path = str(download_dir)

    try:
        path, failure_reason, failure_detail = download_sentinel2(
            footprint_wkt=footprint,
            date_range=date_range,
            cloud_cover=cloud_cover,
            prefer_earliest=prefer_earliest,
            download_dir=download_path,
        )
    except Exception as e:
        path, failure_reason, failure_detail = None, "exception", str(e)

    if not path:
        # Create a failed marker so the UI can report it
        scene = SatelliteScene(
            area=area,
            scene_date=date_start if prefer_earliest else date_end,
            status=SatelliteScene.Status.FAILED,
            product_id="",
        )
        if failure_reason == "no_credentials":
            scene.error_message = (
                "Sentinel 認証が未設定です。.env に CDSE_USERNAME と CDSE_PASSWORD を設定してください。"
            )
        elif failure_reason == "no_products":
            scene.error_message = (
                f"期間 {date_start} ～ {date_end} に L2A 製品が見つかりませんでした（雲量{cloud_cover[1]}%以下）。"
                " 雲量を上げるか、別の期間を試してください。"
            )
        else:
            scene.error_message = failure_detail or failure_reason or "不明なエラー"
        scene.save()
        return scene

    # Determine actual scene date from filename (YYYYMMDDTHHMMSS)
    import re as _re
    stem = Path(path).stem
    m = _re.search(r"_(\d{4})(\d{2})(\d{2})T", stem)
    actual_date = datetime.date.today()
    if m:
        try:
            actual_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # Reuse existing row if one exists for this date, else create
    scene, _ = SatelliteScene.objects.get_or_create(
        area=area,
        scene_date=actual_date,
        defaults={"status": SatelliteScene.Status.PENDING, "product_id": ""},
    )
    try:
        rel = Path(path).relative_to(Path(settings.MEDIA_ROOT))
        scene.file_path = str(rel)
    except ValueError:
        scene.file_path = path
    scene.status = SatelliteScene.Status.DOWNLOADED
    scene.product_id = Path(path).stem[:255]
    scene.error_message = ""
    scene.save(update_fields=["status", "file_path", "product_id", "error_message", "updated_at"])
    return scene


def find_scene_earliest_in_window(
    area: Area,
    date_start: datetime.date,
    date_end: datetime.date,
) -> Optional[SatelliteScene]:
    """指定窓 [date_start, date_end] 内で、取得済みのうち最も早い日付のシーンを1件返す（Before 用）。"""
    return (
        SatelliteScene.objects.filter(
            area=area,
            scene_date__gte=date_start,
            scene_date__lte=date_end,
            status=SatelliteScene.Status.DOWNLOADED,
        )
        .order_by("scene_date")
        .first()
    )


def find_scene_latest_in_window(
    area: Area,
    date_start: datetime.date,
    date_end: datetime.date,
) -> Optional[SatelliteScene]:
    """指定窓 [date_start, date_end] 内で、取得済みのうち最も新しい日付のシーンを1件返す（After 用）。"""
    return (
        SatelliteScene.objects.filter(
            area=area,
            scene_date__gte=date_start,
            scene_date__lte=date_end,
            status=SatelliteScene.Status.DOWNLOADED,
        )
        .order_by("-scene_date")
        .first()
    )
