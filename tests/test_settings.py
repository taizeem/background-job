from django.conf import settings


def test_core_installed_apps():
    """Verifies that essential apps and rest_framework are registered."""
    assert "rest_framework" in settings.INSTALLED_APPS
    assert "apps.core.apps.CoreConfig" in settings.INSTALLED_APPS


def test_timezone_is_utc():
    """Verifies that the project uses UTC timezone."""
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True
