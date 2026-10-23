import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import ApiKey


@pytest.fixture
def auth_user(db):
    """Fixture providing a default test user."""
    User = get_user_model()
    return User.objects.create_user(
        username="default_test_user", password="password123"
    )


@pytest.fixture
def auth_api_key(auth_user):
    """Fixture providing a valid API key for auth_user."""
    _, raw_key = ApiKey.generate(
        user=auth_user,
        name="Default Test Key",
        max_active_jobs=50,
    )
    return raw_key


@pytest.fixture
def api_client(auth_api_key) -> APIClient:
    """
    Fixture providing a DRF APIClient pre-authenticated with auth_api_key.
    Can be cleared in any test with api_client.credentials().
    """
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=auth_api_key)
    return client
