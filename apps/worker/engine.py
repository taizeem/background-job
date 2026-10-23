import logging
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.jobs.constants import AttemptStatus, JobStatus
from apps.jobs.models import Job, JobAttempt

logger = logging.getLogger(__name__)


@dataclass
class WorkerOptions:
    """Configuration options for a WorkerEngine instance."""

    worker_id: str | None = None
    poll_interval: float = 1.0  # Seconds to wait when no jobs are available
    lease_duration_seconds: int = 300  # Default 5-minute lease
    burst: bool = False  # If True, process all ready jobs and exit immediately


class WorkerEngine:
    """
    Dedicated background worker process that claims and executes jobs from PostgreSQL.
    """

    def __init__(self, options: WorkerOptions | None = None) -> None:
        self.options = options or WorkerOptions()
        self.worker_id = self.options.worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.should_stop = False

    def register_signal_handlers(self) -> None:
        """Registers handlers for SIGINT (Ctrl+C) and SIGTERM for graceful shutdown."""
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum: int, frame: Any) -> None:
        logger.info(
            "Worker %s received termination signal (%s). Finishing active job before exit...",
            self.worker_id,
            signum,
        )
        self.should_stop = True

    @staticmethod
    def claim_next_job(worker_id: str, lease_duration_seconds: int = 300) -> Job | None:
        """
        Atomically claims the highest priority, oldest eligible job using
        SELECT ... FOR UPDATE SKIP LOCKED.

        The transaction commits immediately upon updating status to CLAIMED.
        """
        now = timezone.now()
        with transaction.atomic():
            job = (
                Job.objects.select_for_update(skip_locked=True)
                .filter(
                    status__in=(JobStatus.PENDING, JobStatus.RETRYING),
                    scheduled_at__lte=now,
                )
                .order_by("-priority", "scheduled_at", "created_at")
                .first()
            )

            if not job:
                return None

            job.transition_to(JobStatus.CLAIMED)
            job.claimed_by = worker_id
            job.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
            job.save(
                update_fields=[
                    "status",
                    "claimed_by",
                    "lease_expires_at",
                    "updated_at",
                ]
            )
            return job

    def execute_job(self, job: Job) -> bool:
        """
        Executes a claimed job, tracking elapsed time and persisting the result.
        Runs entirely outside of the claiming transaction.
        """
        start_time = time.monotonic()
        attempt_number = job.attempt_count + 1

        # 1. Transition to RUNNING and record attempt start
        with transaction.atomic():
            job.transition_to(JobStatus.RUNNING)
            job.attempt_count = attempt_number
            job.started_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "attempt_count",
                    "started_at",
                    "updated_at",
                ]
            )

        attempt = JobAttempt(
            job=job,
            attempt_number=attempt_number,
            worker_id=self.worker_id,
            status=AttemptStatus.SUCCESS,
            started_at=job.started_at,
        )

        try:
            logger.info("Worker %s executing Job %s (%s)", self.worker_id, job.id, job.job_type)

            # Simulated execution payload processing for Phase 4
            # (Phase 5 introduces the full dynamic handler registry)
            result_data = {
                "message": f"Successfully processed job_type '{job.job_type}'",
                "received_payload": job.payload,
            }

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # 2. Transition to COMPLETED
            with transaction.atomic():
                job.transition_to(JobStatus.COMPLETED)
                job.result = result_data
                job.claimed_by = None
                job.lease_expires_at = None
                job.save(
                    update_fields=[
                        "status",
                        "result",
                        "claimed_by",
                        "lease_expires_at",
                        "completed_at",
                        "updated_at",
                    ]
                )

                attempt.status = AttemptStatus.SUCCESS
                attempt.duration_ms = elapsed_ms
                attempt.completed_at = timezone.now()
                attempt.save()

            logger.info(
                "Worker %s completed Job %s in %.2f ms",
                self.worker_id,
                job.id,
                elapsed_ms,
            )
            return True

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.exception("Worker %s encountered error on Job %s: %s", self.worker_id, job.id, exc)

            with transaction.atomic():
                job.transition_to(JobStatus.FAILED)
                job.error_message = str(exc)
                job.claimed_by = None
                job.lease_expires_at = None
                job.save(
                    update_fields=[
                        "status",
                        "error_message",
                        "claimed_by",
                        "lease_expires_at",
                        "completed_at",
                        "updated_at",
                    ]
                )

                attempt.status = AttemptStatus.FAILURE
                attempt.duration_ms = elapsed_ms
                attempt.error_message = str(exc)
                attempt.completed_at = timezone.now()
                attempt.save()

            return False

    def run(self) -> int:
        """
        Starts the worker polling loop.
        Returns the total number of jobs processed.
        """
        logger.info("Worker %s started (poll_interval=%.2fs)", self.worker_id, self.options.poll_interval)
        self.register_signal_handlers()
        jobs_processed = 0

        while not self.should_stop:
            job = self.claim_next_job(
                worker_id=self.worker_id,
                lease_duration_seconds=self.options.lease_duration_seconds,
            )

            if job:
                self.execute_job(job)
                jobs_processed += 1
            else:
                if self.options.burst:
                    logger.info("Worker %s burst complete: no more ready jobs.", self.worker_id)
                    break
                time.sleep(self.options.poll_interval)

        logger.info("Worker %s stopped. Total jobs processed: %d", self.worker_id, jobs_processed)
        return jobs_processed