import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.jobs.constants import JobStatus
from apps.jobs.exceptions import NonRetryableJobError, RetryableJobError
from apps.jobs.models import Job
from apps.jobs.services.retry_service import RetryService
from apps.worker.engine import WorkerEngine, WorkerOptions


def test_exponential_backoff_math_without_jitter():
    """Verifies pure 2^N exponential backoff progression."""
    # Attempt 1: 2.0 * 2^0 = 2.0s
    assert RetryService.calculate_backoff(attempt_number=1, with_jitter=False) == 2.0
    # Attempt 2: 2.0 * 2^1 = 4.0s
    assert RetryService.calculate_backoff(attempt_number=2, with_jitter=False) == 4.0
    # Attempt 3: 2.0 * 2^2 = 8.0s
    assert RetryService.calculate_backoff(attempt_number=3, with_jitter=False) == 8.0
    # Attempt 4: 2.0 * 2^3 = 16.0s
    assert RetryService.calculate_backoff(attempt_number=4, with_jitter=False) == 16.0


def test_exponential_backoff_respects_max_cap():
    """Verifies that delays do not exceed the max_delay ceiling."""
    delay = RetryService.calculate_backoff(
        attempt_number=15, max_delay=60.0, with_jitter=False
    )
    assert delay == 60.0


def test_full_jitter_bounds():
    """Verifies that Full Jitter produces delays bounded within [0, calculated_cap]."""
    for attempt in range(1, 5):
        cap = 2.0 * (2 ** (attempt - 1))
        # Sample 50 random jitter delays
        for _ in range(50):
            jittered_delay = RetryService.calculate_backoff(
                attempt_number=attempt, with_jitter=True
            )
            assert 0.0 <= jittered_delay <= cap


def test_error_classification():
    """Verifies classification of transient vs permanent errors."""
    assert RetryService.is_retryable(RetryableJobError("timeout")) is True
    assert RetryService.is_retryable(ConnectionError("host down")) is True

    assert RetryService.is_retryable(NonRetryableJobError("bad data")) is False
    assert RetryService.is_retryable(ValueError("invalid format")) is False
    assert RetryService.is_retryable(KeyError("missing_field")) is False


@pytest.mark.django_db
def test_transient_error_schedules_retry():
    """Verifies that a retryable error sets status=RETRYING and computes next_retry_at."""
    job = Job.objects.create(
        job_type="email.send",
        payload={"to": "dev@company.com", "simulate_timeout": True},
        max_attempts=3,
    )
    job.transition_to(JobStatus.CLAIMED)
    job.save()

    engine = WorkerEngine(options=WorkerOptions(worker_id="test-worker"))
    engine.execute_job(job)

    job.refresh_from_db()
    assert job.status == JobStatus.RETRYING
    assert job.attempt_count == 1
    assert job.next_retry_at is not None
    assert "timed out" in job.error_message


@pytest.mark.django_db
def test_non_retryable_error_fails_immediately():
    """Verifies that a permanent error transitions immediately to FAILED without retrying."""
    job = Job.objects.create(
        job_type="email.send",
        payload={"to": "invalid-email"},  # Invalid format = NonRetryableJobError
        max_attempts=3,
    )
    job.transition_to(JobStatus.CLAIMED)
    job.save()

    engine = WorkerEngine(options=WorkerOptions(worker_id="test-worker"))
    engine.execute_job(job)

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.attempt_count == 1
    assert job.next_retry_at is None
    assert job.completed_at is not None


@pytest.mark.django_db
def test_max_attempts_exhaustion_moves_to_dead_letter():
    """Verifies that when attempt_count reaches max_attempts, job transitions to EXHAUSTED."""
    job = Job.objects.create(
        job_type="email.send",
        payload={"to": "dev@company.com", "simulate_timeout": True},
        max_attempts=2,
        attempt_count=1,  # Already failed once
    )
    job.transition_to(JobStatus.CLAIMED)
    job.save()

    engine = WorkerEngine(options=WorkerOptions(worker_id="test-worker"))
    engine.execute_job(job)  # This will be attempt #2 of 2

    job.refresh_from_db()
    assert job.status == JobStatus.EXHAUSTED
    assert job.attempt_count == 2
    assert "Max attempts (2) exhausted" in job.error_message
    assert job.completed_at is not None


@pytest.mark.django_db
def test_manual_retry_revives_exhausted_job(api_client: APIClient):
    """Verifies POST /api/v1/jobs/<id>/retry/ resets an EXHAUSTED job back to PENDING."""
    job = Job.objects.create(
        job_type="email.send",
        status=JobStatus.EXHAUSTED,
        attempt_count=3,
        error_message="Exhausted",
    )
    url = reverse("job-retry", kwargs={"job_id": job.id})

    response = api_client.post(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == JobStatus.PENDING
    assert response.json()["attempt_count"] == 0

    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.attempt_count == 0
    assert job.error_message is None
