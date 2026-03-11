from django.apps import AppConfig


class DetectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "detections"
    verbose_name = "Detections (検出ジョブ・結果)"

    def ready(self):
        from . import signals  # noqa: F401
