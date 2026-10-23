import json
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


class QuotaExceededError(Exception):
    """Raised when a user exceeds their allowed active job quota."""


class PayloadTooLargeError(Exception):
    """Raised when a job payload exceeds maximum byte size limits."""


@dataclass(frozen=True)
class JobCreationResult:
    """Encapsulates the outcome of a job submission."""

    job: Job
    is_created: bool


class JobService:
    """Service layer managing job persistence, deduplication, and lifecycle operations."""

    MAX_PAYLOAD_BYTES: int = 65_536  # 64 KB maximum payload size

    @classmethod
    def create_job(
        cls,
        *,
        job_type: str,
        user=None,
        payload: dict[str, Any] | None = None,
        priority: int = JobPriority.DEFAULT,
        max_attempts: int = 3,
        scheduled_at: datetime | None = None,
        idempotency_key: str | None = None,
        max_active_jobs: int = 10,
    ) -> JobCreationResult:
        """
        Creates a new job or returns an existing one if an identical idempotency key is matched.
        Enforces payload size constraints and per-user active job quotas.
        """
        clean_payload = payload if payload is not None else {}
        effective_scheduled_at = (
            scheduled_at if scheduled_at is not None else timezone.now()
        )

        # 1. Enforce payload size limit
        payload_bytes = len(json.dumps(clean_payload).encode("utf-8"))
        if payload_bytes > cls.MAX_PAYLOAD_BYTES:
            raise PayloadTooLargeError(
                f"Payload size ({payload_bytes} bytes) exceeds maximum limit of {cls.MAX_PAYLOAD_BYTES} bytes."
            )

        with transaction.atomic():
            # 2. Check idempotency key first
            if idempotency_key:
                existing_job = (
                    Job.objects.select_for_update()
                    .filter(idempotency_key=idempotency_key)
                    .first()
                )

                if existing_job:
                    if (
                        existing_job.job_type != job_type
                        or existing_job.payload != clean_payload
                    ):
                        raise IdempotencyConflictError(
                            f"Idempotency key '{idempotency_key}' was already used with a different payload or job_type."
                        )
                    return JobCreationResult(job=existing_job, is_created=False)

            # 3. Enforce active job quota for authenticated user
            if user:
                active_job_count = Job.objects.filter(
                    user=user,
                    status__in=(
                        JobStatus.PENDING,
                        JobStatus.CLAIMED,
                        JobStatus.RUNNING,
                        JobStatus.RETRYING,
                    ),
                ).count()

                if active_job_count >= max_active_jobs:
                    raise QuotaExceededError(
                        f"Active job quota exceeded. You currently have {active_job_count} active jobs (maximum allowed: {max_active_jobs})."
                    )

            # 4. Insert new job
            job = Job.objects.create(
                user=user,
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
    def cancel_job(job_id: Any, user=None) -> Job:
        """
        Cancels an eligible job, verifying user ownership if a user is provided.
        """
        with transaction.atomic():
            query = Job.objects.select_for_update().filter(id=job_id)
            if user and not user.is_staff:
                query = query.filter(user=user)

            job = query.first()
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
