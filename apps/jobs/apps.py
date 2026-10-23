from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jobs"
    verbose_name = "Jobs"

    def ready(self) -> None:
        """Autodiscover job handlers on startup."""
        import apps.jobs.handlers  # noqa: F401
