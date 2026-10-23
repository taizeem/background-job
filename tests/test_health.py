from unittest.mock import patch

import pytest
from django.db.utils import OperationalError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_live_endpoint_returns_ok(api_client: APIClient):
    """
    Verifies that /api/health/live/ returns HTTP 200 with status: healthy.
    """
    url = reverse("health-live")
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "healthy"}


@pytest.mark.django_db
def test_health_ready_endpoint_success_when_database_is_up(api_client: APIClient):
    """
    Verifies that /api/health/ready/ returns HTTP 200 when database queries succeed.
    """
    url = reverse("health-ready")
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ready"
    assert data["dependencies"]["database"] == "connected"


@pytest.mark.django_db
def test_health_ready_endpoint_fails_when_database_is_down(api_client: APIClient):
    """
    Verifies that /api/health/ready/ returns HTTP 503 when a database error occurs.
    """
    url = reverse("health-ready")

    # Simulate a database outage by causing cursor.execute to raise OperationalError
    with patch("django.db.connection.cursor") as mock_cursor:
        mock_cursor.side_effect = OperationalError("Could not connect to server")
        response = api_client.get(url)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["dependencies"]["database"] == "unreachable"
