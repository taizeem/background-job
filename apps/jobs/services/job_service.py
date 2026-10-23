from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.jobs.constants import JobPriority, JobStatus
from apps.jobs.models import Job


class IdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a differing payload or job_type."""


class JobCancellationError(Exception):
    """Raised when a job cannot be cancelled due to its current lifecycle state."""


@dataclass(frozen=True)
class JobCreationResult:
    """Encapsulates the outcome of a job submission."""

    job: Job
    is_created: (
        bool  # True if newly inserted, False if resolved via existing idempotency key
    )


class JobService:
    """Service layer managing job persistence, deduplication, and lifecycle operations."""

    @staticmethod
    def create_job(
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        priority: int = JobPriority.DEFAULT,
        max_attempts: int = 3,
        scheduled_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> JobCreationResult:
        """
        Creates a new job or returns an existing one if an identical idempotency key is matched.
        Uses atomic transactions to ensure data integrity.
        """
        clean_payload = payload if payload is not None else {}
        effective_scheduled_at = (
            scheduled_at if scheduled_at is not None else timezone.now()
        )

        with transaction.atomic():
            if idempotency_key:
                existing_job = (
                    Job.objects.select_for_update()
                    .filter(idempotency_key=idempotency_key)
                    .first()
                )

                if existing_job:
                    # Verify that the reused key matches the original job parameters
                    if (
                        existing_job.job_type != job_type
                        or existing_job.payload != clean_payload
                    ):
                        raise IdempotencyConflictError(
                            f"Idempotency key '{idempotency_key}' was already used with a different payload or job_type."
                        )
                    return JobCreationResult(job=existing_job, is_created=False)

            job = Job.objects.create(
                job_type=job_type,
                payload=clean_payload,
                priority=priority,
                max_attempts=max_attempts,
                scheduled_at=effective_scheduled_at,
                idempotency_key=idempotency_key,
                status=JobStatus.PENDING,
            )
            return JobCreationResult(job=job, is_created=True)

    @staticmethod
    def cancel_job(job_id: Any) -> Job:
        """
        Cancels an eligible job (in PENDING or RETRYING status).
        Raises JobCancellationError if the job is already terminal or currently running.
        """
        with transaction.atomic():
            job = Job.objects.select_for_update().filter(id=job_id).first()
            if not job:
                raise Job.DoesNotExist(f"Job with ID {job_id} does not exist.")

            if job.status == JobStatus.RUNNING:
                raise JobCancellationError(
                    "Cannot cancel a job that is currently RUNNING. Wait for completion or lease timeout."
                )

            if job.is_terminal:
                raise JobCancellationError(
                    f"Cannot cancel a job that is already in terminal state '{job.status}'."
                )

            try:
                job.transition_to(JobStatus.CANCELLED)
                job.save(update_fields=["status", "completed_at", "updated_at"])
            except ValidationError as exc:
                raise JobCancellationError(str(exc)) from exc

            return job
