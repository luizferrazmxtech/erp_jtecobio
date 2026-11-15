from django.apps import AppConfig


class WebConfig(AppConfig):
    """Telas do sistema (painel, listas e formulários) fora do admin."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.web"
