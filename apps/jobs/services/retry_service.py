"""
Retry management service with exponential backoff and jitter calculations.
"""

import logging
import random
from datetime import timedelta

from django.utils import timezone

from apps.jobs.constants import JobStatus
from apps.jobs.exceptions import NonRetryableJobError, RetryableJobError
from apps.jobs.models import Job, JobAttempt

logger = logging.getLogger(__name__)


class RetryService:
    """Manages failure handling, backoff scheduling, and dead-letter queue routing."""

    DEFAULT_BASE_DELAY_SECONDS: float = 2.0
    DEFAULT_MAX_DELAY_SECONDS: float = 300.0  # 5-minute cap

    @classmethod
    def calculate_backoff(
        cls,
        attempt_number: int,
        base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
        max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
        with_jitter: bool = True,
    ) -> float:
        """
        Calculates exponential backoff delay with Full Jitter:
        max_backoff = min(max_delay, base_delay * (2 ** (attempt - 1)))
        delay = uniform(0, max_backoff)
        """
        if attempt_number <= 0:
            return 0.0

        exponential_factor = 2 ** max(0, attempt_number - 1)
        calculated_cap = min(max_delay, base_delay * exponential_factor)

        if with_jitter:
            # Full Jitter: evenly distributed between 0 and calculated_cap
            return random.uniform(0.0, calculated_cap)

        return calculated_cap

    @classmethod
    def is_retryable(cls, exc: Exception) -> bool:
        """
        Determines whether an exception is considered transient/retryable.
        """
        if isinstance(exc, NonRetryableJobError):
            return False
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            # Standard Python data errors are inherently non-retryable
            return False
        if isinstance(exc, RetryableJobError):
            return True

        # By default, unknown operational exceptions are considered transient
        return True

    @classmethod
    def handle_failure(
        cls,
        job: Job,
        attempt: JobAttempt,
        exc: Exception,
        stack_trace: str,
    ) -> str:
        """
        Applies failure logic to the job:
        - Non-retryable error -> FAILED
        - Retryable & attempt < max_attempts -> RETRYING (with scheduled backoff)
        - Retryable & attempt >= max_attempts -> EXHAUSTED (Dead-Letter Queue)
        """
        attempt_number = job.attempt_count
        retryable = cls.is_retryable(exc)

        if not retryable:
            # Permanent failure: move immediately to FAILED
            job.transition_to(JobStatus.FAILED)
            job.error_message = str(exc)
            job.error_traceback = stack_trace
            job.claimed_by = None
            job.lease_expires_at = None
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "error_traceback",
                    "claimed_by",
                    "lease_expires_at",
                    "completed_at",
                    "updated_at",
                ]
            )
            logger.info(
                "Job %s failed permanently with non-retryable error: %s",
                job.id,
                exc,
            )
            return JobStatus.FAILED

        if attempt_number < job.max_attempts:
            # Transient failure within budget: schedule next retry
            delay_seconds = cls.calculate_backoff(attempt_number=attempt_number)
            next_retry = timezone.now() + timedelta(seconds=delay_seconds)

            job.transition_to(JobStatus.RETRYING)
            job.error_message = str(exc)
            job.error_traceback = stack_trace
            job.claimed_by = None
            job.lease_expires_at = None
            job.next_retry_at = next_retry
            job.scheduled_at = next_retry  # Keeps job unclaimable until delay elapses
            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "error_traceback",
                    "claimed_by",
                    "lease_expires_at",
                    "next_retry_at",
                    "scheduled_at",
                    "updated_at",
                ]
            )
            logger.info(
                "Job %s scheduled for retry #%d in %.2fs (at %s)",
                job.id,
                attempt_number + 1,
                delay_seconds,
                next_retry.isoformat(),
            )
            return JobStatus.RETRYING

        # Max attempts exceeded: move to EXHAUSTED (Dead-Letter state)
        job.transition_to(JobStatus.EXHAUSTED)
        job.error_message = f"Max attempts ({job.max_attempts}) exhausted. Last error: {exc}"
        job.error_traceback = stack_trace
        job.claimed_by = None
        job.lease_expires_at = None
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_message",
                "error_traceback",
                "claimed_by",
                "lease_expires_at",
                "completed_at",
                "updated_at",
            ]
        )
        logger.warning(
            "Job %s permanently exhausted after %d attempts. Moved to Dead-Letter state.",
            job.id,
            attempt_number,
        )
        return JobStatus.EXHAUSTED