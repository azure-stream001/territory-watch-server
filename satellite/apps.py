from django.apps import AppConfig


class SatelliteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "satellite"
    verbose_name = "Satellite (衛星・変化検出)"

    def ready(self):
        from . import signals  # noqa: F401
