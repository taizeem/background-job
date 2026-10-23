import pytest

from apps.jobs.constants import JobStatus
from apps.jobs.services.job_service import (
    IdempotencyConflictError,
    JobCancellationError,
    JobService,
)


@pytest.mark.django_db
def test_job_service_creates_new_job():
    """Verifies that JobService creates a job when no key is specified."""
    result = JobService.create_job(
        job_type="reports.generate",
        payload={"report_id": 101},
    )

    assert result.is_created is True
    assert result.job.status == JobStatus.PENDING
    assert result.job.payload == {"report_id": 101}


@pytest.mark.django_db
def test_job_service_idempotency_returns_existing_job():
    """Verifies that submitting the exact same key and payload returns the existing job."""
    key = "unique-key-abc-123"

    result_1 = JobService.create_job(
        job_type="email.send",
        payload={"to": "user@example.com"},
        idempotency_key=key,
    )
    assert result_1.is_created is True

    result_2 = JobService.create_job(
        job_type="email.send",
        payload={"to": "user@example.com"},
        idempotency_key=key,
    )
    assert result_2.is_created is False
    assert result_1.job.id == result_2.job.id


@pytest.mark.django_db
def test_job_service_idempotency_conflict_raises_error():
    """Verifies that reusing an idempotency key with a different payload raises IdempotencyConflictError."""
    key = "unique-key-mismatch"

    JobService.create_job(
        job_type="email.send",
        payload={"to": "user1@example.com"},
        idempotency_key=key,
    )

    with pytest.raises(
        IdempotencyConflictError, match="already used with a different payload"
    ):
        JobService.create_job(
            job_type="email.send",
            payload={"to": "user2@example.com"},  # Different payload!
            idempotency_key=key,
        )


@pytest.mark.django_db
def test_job_service_cancels_pending_job():
    """Verifies that a PENDING job can be successfully cancelled."""
    result = JobService.create_job(job_type="email.send")
    cancelled_job = JobService.cancel_job(result.job.id)

    assert cancelled_job.status == JobStatus.CANCELLED
    assert cancelled_job.completed_at is not None


@pytest.mark.django_db
def test_job_service_cancel_running_job_fails():
    """Verifies that attempting to cancel a RUNNING job raises JobCancellationError."""
    result = JobService.create_job(job_type="email.send")
    job = result.job
    job.transition_to(JobStatus.CLAIMED)
    job.transition_to(JobStatus.RUNNING)
    job.save()

    with pytest.raises(JobCancellationError, match="currently RUNNING"):
        JobService.cancel_job(job.id)
