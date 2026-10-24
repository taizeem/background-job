import importlib
import os
from unittest.mock import patch

from django.conf import settings

from config.wsgi import application


def test_wsgi_application_loads():
    """Verifies that the WSGI application callable is properly initialized."""
    assert application is not None
    assert callable(application)


def test_production_settings_security_flags():
    """Verifies that config.settings.production enforces strict security defaults."""
    with patch.dict(
        os.environ,
        {
            "DJANGO_SECRET_KEY": "prod-test-secret-key-xyz-987654321",
            "DJANGO_SECURE_SSL_REDIRECT": "True",
            "DJANGO_SESSION_COOKIE_SECURE": "True",
            "DJANGO_CSRF_COOKIE_SECURE": "True",
        },
    ):
        import config.settings.production as prod_settings

        importlib.reload(prod_settings)

        assert prod_settings.DEBUG is False
        assert prod_settings.SECURE_SSL_REDIRECT is True
        assert prod_settings.SESSION_COOKIE_SECURE is True
        assert prod_settings.CSRF_COOKIE_SECURE is True
        assert prod_settings.SECURE_HSTS_SECONDS == 31536000
        assert prod_settings.X_FRAME_OPTIONS == "DENY"


def test_dockerfile_and_ci_workflow_exist():
    """Verifies that Dockerfile and GitHub Actions CI configuration are present and hardened."""
    dockerfile_path = settings.BASE_DIR / "Dockerfile"
    ci_path = settings.BASE_DIR / ".github" / "workflows" / "ci.yaml"

    assert dockerfile_path.exists(), "Dockerfile must exist at project root"
    assert ci_path.exists(), "GitHub Actions CI workflow must exist"

    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile_content, (
        "Dockerfile must run as non-root appuser"
    )
    assert "uv sync --frozen" in dockerfile_content, (
        "Dockerfile must use locked dependencies"
    )
