from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.throttling import RedisRateThrottle
from apps.core.redis import is_redis_available
from apps.jobs.constants import JobStatus
from apps.jobs.models import Job
from apps.jobs.services.metrics_service import MetricsService


def test_redis_connection_healthy():
    """Verifies that Redis is accessible and responding to PING."""
    assert is_redis_available() is True


@pytest.mark.django_db
def test_redis_sliding_window_rate_limiting(api_client: APIClient):
    """
    Verifies that exceeding the sliding window threshold returns HTTP 429 with Retry-After.
    """
    url = reverse("job-list-create")
    # Temporarily set a low limit of 3 requests for testing
    with patch.object(RedisRateThrottle, "rate_limit", 3):
        # Requests 1, 2, 3 succeed
        for _ in range(3):
            res = api_client.post(
                url,
                data={"job_type": "reports.generate", "payload": {}},
                format="json",
            )
            assert res.status_code == status.HTTP_201_CREATED

        # 4th request must be rate-limited
        res4 = api_client.post(
            url,
            data={"job_type": "reports.generate", "payload": {}},
            format="json",
        )
        assert res4.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Retry-After" in res4.headers


@pytest.mark.django_db
def test_rate_limiter_fails_open_when_redis_down(api_client: APIClient):
    """
    Verifies graceful degradation: if Redis is unreachable, requests are allowed.
    """
    url = reverse("job-list-create")

    with patch("apps.accounts.throttling.get_redis_client") as mock_client:
        mock_client.side_effect = ConnectionError("Could not reach Redis")

        response = api_client.post(
            url,
            data={"job_type": "reports.generate", "payload": {}},
            format="json",
        )
        # Must fail open (allow request) rather than returning HTTP 500
        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_metrics_service_caches_in_redis():
    """
    Verifies that MetricsService queries PostgreSQL on miss, then serves from Redis.
    """
    Job.objects.create(job_type="reports.generate", status=JobStatus.PENDING)
    Job.objects.create(job_type="email.send", status=JobStatus.COMPLETED)

    # First fetch: live from PostgreSQL
    metrics1 = MetricsService.get_queue_metrics()
    assert metrics1["source"] == "postgresql_live"
    assert metrics1["queue_depths"]["pending"] == 1
    assert metrics1["queue_depths"]["completed"] == 1

    # Second fetch: served instantly from Redis cache
    metrics2 = MetricsService.get_queue_metrics()
    assert metrics2["source"] == "redis_cache"
    assert metrics2["queue_depths"]["pending"] == 1


@pytest.mark.django_db
def test_metrics_endpoint_authenticated(api_client: APIClient):
    """Verifies GET /api/v1/jobs/metrics/ returns queue statistics."""
    url = reverse("job-metrics")
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "queue_depths" in data
    assert "total_jobs" in data