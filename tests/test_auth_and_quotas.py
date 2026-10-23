import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import ApiKey
from apps.jobs.constants import JobStatus
from apps.jobs.models import Job

User = get_user_model()


@pytest.fixture
def test_user():
    return User.objects.create_user(username="tenant_a", password="password123")


@pytest.fixture
def other_user():
    return User.objects.create_user(username="tenant_b", password="password123")


@pytest.fixture
def api_key_a(test_user):
    _, raw_key = ApiKey.generate(user=test_user, name="Tenant A Key", max_active_jobs=2)
    return raw_key


@pytest.fixture
def api_key_b(other_user):
    _, raw_key = ApiKey.generate(
        user=other_user, name="Tenant B Key", max_active_jobs=5
    )
    return raw_key


@pytest.mark.django_db
def test_unauthenticated_request_rejected(api_client: APIClient):
    """Verifies that requests without an API key receive HTTP 401."""
    api_client.credentials()  # Clear credentials
    url = reverse("job-list-create")
    response = api_client.get(url)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_invalid_api_key_rejected(api_client: APIClient):
    """Verifies that an invalid API key receives HTTP 401."""
    url = reverse("job-list-create")
    api_client.credentials(HTTP_X_API_KEY="jp_live_invalid_key_string")
    response = api_client.get(url)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_authenticated_submission_success(api_client: APIClient, api_key_a, test_user):
    """Verifies that a valid API key submits a job associated with the tenant."""
    url = reverse("job-list-create")
    api_client.credentials(HTTP_X_API_KEY=api_key_a)

    response = api_client.post(
        url,
        data={"job_type": "reports.generate", "payload": {"year": 2026}},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    job_id = response.json()["id"]

    job = Job.objects.get(id=job_id)
    assert job.user == test_user


@pytest.mark.django_db
def test_tenant_data_isolation(
    api_client: APIClient, api_key_a, api_key_b, test_user, other_user
):
    """
    Verifies that User A cannot see or access jobs belonging to User B.
    """
    job_b = Job.objects.create(job_type="email.send", user=other_user)

    # User A tries to GET User B's job detail
    api_client.credentials(HTTP_X_API_KEY=api_key_a)
    url = reverse("job-detail", kwargs={"job_id": job_b.id})
    response = api_client.get(url)

    # Must return 404 Not Found to prevent resource enumeration
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_active_job_quota_enforcement(api_client: APIClient, api_key_a, test_user):
    """
    Verifies that when a user reaches their max_active_jobs quota (2 for Tenant A),
    subsequent submissions return HTTP 429 Too Many Requests.
    """
    api_client.credentials(HTTP_X_API_KEY=api_key_a)
    url = reverse("job-list-create")

    # Create 2 active jobs (reaches the quota of 2)
    Job.objects.create(job_type="email.send", user=test_user, status=JobStatus.PENDING)
    Job.objects.create(job_type="email.send", user=test_user, status=JobStatus.RUNNING)

    # 3rd submission must be throttled
    response = api_client.post(
        url,
        data={"job_type": "reports.generate", "payload": {}},
        format="json",
    )

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Active job quota exceeded" in response.json()["error"]


@pytest.mark.django_db
def test_payload_size_limit_rejected(api_client: APIClient, api_key_a):
    """Verifies that payloads exceeding 64 KB return HTTP 413."""
    api_client.credentials(HTTP_X_API_KEY=api_key_a)
    url = reverse("job-list-create")

    # Construct oversized payload (>65,536 bytes)
    large_text = "x" * 70_000
    response = api_client.post(
        url,
        data={"job_type": "files.process", "payload": {"big_data": large_text}},
        format="json",
    )

    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert "exceeds maximum limit" in response.json()["error"]


@pytest.mark.django_db
def test_health_endpoints_remain_public(api_client: APIClient):
    """Verifies that health probes do not require authentication."""
    live_url = reverse("health-live")
    ready_url = reverse("health-ready")

    assert api_client.get(live_url).status_code == status.HTTP_200_OK
    assert api_client.get(ready_url).status_code == status.HTTP_200_OK
