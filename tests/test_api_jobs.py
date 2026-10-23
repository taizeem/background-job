import uuid

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.jobs.constants import JobPriority, JobStatus
from apps.jobs.models import Job


@pytest.mark.django_db
def test_submit_job_success(api_client: APIClient):
    """Verifies POST /api/v1/jobs/ creates a job and returns HTTP 201."""
    url = reverse("job-list-create")
    data = {
        "job_type": "reports.generate",
        "payload": {"month": "November", "year": 2026},
        "priority": JobPriority.HIGH,
        "max_attempts": 5,
    }

    response = api_client.post(url, data=data, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    res_data = response.json()
    assert res_data["job_type"] == "reports.generate"
    assert res_data["status"] == JobStatus.PENDING
    assert res_data["priority"] == JobPriority.HIGH
    assert res_data["max_attempts"] == 5
    assert res_data["payload"] == {"month": "November", "year": 2026}


@pytest.mark.django_db
def test_submit_unregistered_job_type_fails(api_client: APIClient):
    """Verifies POST /api/v1/jobs/ rejects unregistered job types with HTTP 400."""
    url = reverse("job-list-create")
    data = {
        "job_type": "malicious.arbitrary_code_exec",
        "payload": {},
    }

    response = api_client.post(url, data=data, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unsupported job_type" in str(response.json())


@pytest.mark.django_db
def test_submit_job_idempotency_returns_200(api_client: APIClient):
    """Verifies that repeating a request with the same idempotency key returns HTTP 200 with identical ID."""
    url = reverse("job-list-create")
    key = str(uuid.uuid4())
    data = {
        "job_type": "email.send",
        "payload": {"recipient": "dev@company.com"},
        "idempotency_key": key,
    }

    # First submission: 201 Created
    res1 = api_client.post(url, data=data, format="json")
    assert res1.status_code == status.HTTP_201_CREATED
    job_id_1 = res1.json()["id"]

    # Replayed submission: 200 OK
    res2 = api_client.post(url, data=data, format="json")
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["id"] == job_id_1


@pytest.mark.django_db
def test_submit_job_idempotency_conflict_returns_409(api_client: APIClient):
    """Verifies that reusing a key with different payload returns HTTP 409 Conflict."""
    url = reverse("job-list-create")
    key = "shared-request-key-xyz"

    api_client.post(
        url,
        data={
            "job_type": "email.send",
            "payload": {"foo": "bar"},
            "idempotency_key": key,
        },
        format="json",
    )

    # Submit again with different payload
    res = api_client.post(
        url,
        data={
            "job_type": "email.send",
            "payload": {"foo": "DIFFERENT"},
            "idempotency_key": key,
        },
        format="json",
    )

    assert res.status_code == status.HTTP_409_CONFLICT
    assert "already used with a different payload" in res.json()["error"]


@pytest.mark.django_db
def test_get_job_detail_success(api_client: APIClient, auth_user):
    """Verifies GET /api/v1/jobs/<id>/ returns complete job detail for the owner."""
    job = Job.objects.create(
        user=auth_user,
        job_type="reports.generate",
        payload={"format": "csv"},
    )
    url = reverse("job-detail", kwargs={"job_id": job.id})

    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == str(job.id)
    assert response.json()["payload"] == {"format": "csv"}


@pytest.mark.django_db
def test_get_job_detail_not_found(api_client: APIClient):
    """Verifies GET /api/v1/jobs/<non_existent_id>/ returns HTTP 404."""
    url = reverse("job-detail", kwargs={"job_id": uuid.uuid4()})
    response = api_client.get(url)

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_list_jobs_and_filter_by_status(api_client: APIClient, auth_user):
    """Verifies listing jobs with query param filtering for the owning user."""
    Job.objects.create(
        user=auth_user, job_type="reports.generate", status=JobStatus.PENDING
    )
    Job.objects.create(
        user=auth_user, job_type="email.send", status=JobStatus.COMPLETED
    )

    url = reverse("job-list-create")

    # Filter by status=PENDING
    response = api_client.get(url, {"status": "PENDING"})

    assert response.status_code == status.HTTP_200_OK
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == JobStatus.PENDING


@pytest.mark.django_db
def test_cancel_job_endpoint(api_client: APIClient, auth_user):
    """Verifies POST /api/v1/jobs/<id>/cancel/ transitions the owner's job to CANCELLED."""
    job = Job.objects.create(
        user=auth_user, job_type="files.process", status=JobStatus.PENDING
    )
    url = reverse("job-cancel", kwargs={"job_id": job.id})

    response = api_client.post(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == JobStatus.CANCELLED

    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
