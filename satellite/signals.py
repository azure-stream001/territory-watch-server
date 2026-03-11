"""
Clean up scene-related files when a SatelliteScene is deleted.
Removes preview/tiles directory and the product zip (or SAFE dir) if no other scene uses it.
"""
import shutil
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import SatelliteScene


def _preview_dir_for_scene(scene_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / "scene_previews" / str(scene_id)


def _product_path(file_path: str) -> Optional[Path]:
    if not (file_path or "").strip():
        return None
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / file_path
    return p if p.exists() else None


@receiver(post_delete, sender=SatelliteScene)
def delete_scene_files(sender, instance: SatelliteScene, **kwargs):
    """Remove scene preview/tiles and product file when a scene is deleted."""
    # 1. Delete preview directory (map.png, bounds.json, tiles/)
    preview_dir = _preview_dir_for_scene(instance.id)
    if preview_dir.exists():
        try:
            shutil.rmtree(preview_dir)
        except OSError:
            pass

    # 2. Delete product file (zip or .SAFE dir) if under MEDIA_ROOT and no other scene uses it
    file_path = (instance.file_path or "").strip()
    if not file_path:
        return
    abs_path = _product_path(file_path)
    if abs_path is None:
        return
    try:
        media_root = Path(settings.MEDIA_ROOT).resolve()
        abs_path = abs_path.resolve()
        if not str(abs_path).startswith(str(media_root)):
            return
    except (ValueError, OSError):
        return

    # Only delete if no other scene references the same path
    others = SatelliteScene.objects.filter(file_path=file_path).exists()
    if others:
        return

    try:
        if abs_path.is_file():
            abs_path.unlink()
        elif abs_path.is_dir():
            shutil.rmtree(abs_path)
    except OSError:
        pass
