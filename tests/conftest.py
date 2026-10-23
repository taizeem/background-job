import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client() -> APIClient:
    """Fixture providing a fresh DRF APIClient for HTTP requests."""
    return APIClient()
