from django.apps import AppConfig


class OcrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ocr"

    def ready(self):
        # Import every engine module so the @register decorators run.
        from ocr.engines import registry

        registry.load_engines()
