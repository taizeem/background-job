import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.jobs.constants import AttemptStatus, JobPriority, JobStatus
from apps.jobs.models import Job, JobAttempt


@pytest.mark.django_db
def test_create_job_with_defaults():
    """Verifies that a job is created with expected default fields and UUID."""
    job = Job.objects.create(
        job_type="reports.generate",
        payload={"report_type": "monthly", "year": 2026},
    )

    assert isinstance(job.id, uuid.UUID)
    assert job.status == JobStatus.PENDING
    assert job.priority == JobPriority.DEFAULT
    assert job.attempt_count == 0
    assert job.max_attempts == 3
    assert job.is_terminal is False
    assert job.completed_at is None
    assert job.payload["report_type"] == "monthly"


@pytest.mark.django_db
def test_valid_state_machine_flow():
    """Verifies standard valid lifecycle transitions: PENDING -> CLAIMED -> RUNNING -> COMPLETED."""
    job = Job.objects.create(job_type="email.send")
    assert job.status == JobStatus.PENDING

    # Transition to CLAIMED
    job.transition_to(JobStatus.CLAIMED)
    job.save()
    assert job.status == JobStatus.CLAIMED

    # Transition to RUNNING
    job.transition_to(JobStatus.RUNNING)
    job.save()
    assert job.status == JobStatus.RUNNING

    # Transition to COMPLETED
    job.transition_to(JobStatus.COMPLETED)
    job.save()
    assert job.status == JobStatus.COMPLETED
    assert job.is_terminal is True
    assert job.completed_at is not None


@pytest.mark.django_db
def test_invalid_state_machine_transition_raises_validation_error():
    """Verifies that jumping from PENDING directly to COMPLETED raises a ValidationError."""
    job = Job.objects.create(job_type="email.send")

    with pytest.raises(
        ValidationError, match="Illegal state transition from PENDING to COMPLETED"
    ):
        job.transition_to(JobStatus.COMPLETED)


@pytest.mark.django_db
def test_terminal_state_cannot_transition():
    """Verifies that once a job is in a terminal state, no further transitions are allowed."""
    job = Job.objects.create(job_type="email.send")
    job.transition_to(JobStatus.CANCELLED)
    job.save()

    assert job.is_terminal is True
    with pytest.raises(
        ValidationError, match="Illegal state transition from CANCELLED to PENDING"
    ):
        job.transition_to(JobStatus.PENDING)


@pytest.mark.django_db
def test_database_check_constraint_prevents_negative_attempts():
    """Verifies that the PostgreSQL CHECK constraint rejects negative attempt_count."""
    job = Job(job_type="test.job", attempt_count=0)
    job.save()

    # Attempting to persist a negative attempt count must violate database constraint
    with pytest.raises(IntegrityError):
        Job.objects.filter(id=job.id).update(attempt_count=-1)


@pytest.mark.django_db
def test_database_check_constraint_prevents_zero_max_attempts():
    """Verifies that max_attempts must be at least 1."""
    with pytest.raises(IntegrityError):
        Job.objects.create(job_type="test.job", max_attempts=0)


@pytest.mark.django_db
def test_job_attempt_creation_and_cascade_delete():
    """Verifies creating execution attempt records linked to a Job and cascade deletion."""
    job = Job.objects.create(job_type="image.resize")

    attempt_1 = JobAttempt.objects.create(
        job=job,
        attempt_number=1,
        worker_id="worker-node-1-pid-1042",
        status=AttemptStatus.FAILURE,
        duration_ms=145.2,
        error_message="Connection timed out",
    )

    attempt_2 = JobAttempt.objects.create(
        job=job,
        attempt_number=2,
        worker_id="worker-node-2-pid-2088",
        status=AttemptStatus.SUCCESS,
        duration_ms=98.5,
    )

    assert job.attempts.count() == 2
    assert list(job.attempts.all()) == [attempt_1, attempt_2]

    # Deleting the job must cascade delete its audit attempts
    job_id = job.id
    job.delete()
    assert JobAttempt.objects.filter(job_id=job_id).count() == 0


@pytest.mark.django_db
def test_unique_attempt_number_per_job():
    """Verifies that two attempts for the same job cannot have the same attempt_number."""
    job = Job.objects.create(job_type="test.job")
    JobAttempt.objects.create(
        job=job,
        attempt_number=1,
        worker_id="worker-1",
        status=AttemptStatus.SUCCESS,
    )

    with pytest.raises(IntegrityError):
        JobAttempt.objects.create(
            job=job,
            attempt_number=1,
            worker_id="worker-2",
            status=AttemptStatus.SUCCESS,
        )
