import logging
import signal
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from redis.exceptions import RedisError

from apps.core.redis import get_redis_client
from apps.jobs.constants import AttemptStatus, JobStatus
from apps.jobs.models import Job, JobAttempt
from apps.jobs.registry import get_handler
from apps.jobs.services.retry_service import RetryService

logger = logging.getLogger(__name__)


@dataclass
class WorkerOptions:
    """Configuration options for a WorkerEngine instance."""

    worker_id: str | None = None
    poll_interval: float = 1.0
    lease_duration_seconds: int = 300
    burst: bool = False


class WorkerEngine:
    """
    Dedicated background worker process that claims and executes jobs from PostgreSQL.
    """

    HEARTBEAT_TTL_SECONDS: int = 30

    def __init__(self, options: WorkerOptions | None = None) -> None:
        self.options = options or WorkerOptions()
        self.worker_id = self.options.worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.should_stop = False

    def register_signal_handlers(self) -> None:
        """Registers handlers for SIGINT (Ctrl+C) and SIGTERM when running in the main thread."""
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum: int, frame: Any) -> None:
        logger.info(
            "Worker %s received termination signal (%s). Finishing active job before exit...",
            self.worker_id,
            signum,
        )
        self.should_stop = True

    def send_heartbeat(self) -> None:
        """Records an ephemeral worker heartbeat in Redis with a 30s TTL."""
        try:
            client = get_redis_client()
            client.setex(
                f"worker:heartbeat:{self.worker_id}",
                self.HEARTBEAT_TTL_SECONDS,
                timezone.now().isoformat(),
            )
        except (RedisError, OSError) as exc:
            logger.debug(
                "Worker %s could not publish Redis heartbeat: %s", self.worker_id, exc
            )

    @staticmethod
    def renew_lease(
        job: Job, worker_id: str, lease_duration_seconds: int = 300
    ) -> bool:
        """
        Extends the lease_expires_at timestamp for a job currently owned by worker_id.
        Returns True if the lease was extended, False if ownership was lost.
        """
        new_expiry = timezone.now() + timedelta(seconds=lease_duration_seconds)
        updated_rows = Job.objects.filter(
            id=job.id,
            claimed_by=worker_id,
            status__in=(JobStatus.CLAIMED, JobStatus.RUNNING),
        ).update(
            lease_expires_at=new_expiry,
            updated_at=timezone.now(),
        )
        if updated_rows > 0:
            job.lease_expires_at = new_expiry
            return True
        return False

    @staticmethod
    def claim_next_job(worker_id: str, lease_duration_seconds: int = 300) -> Job | None:
        """
        Atomically claims the highest priority, oldest eligible job using
        SELECT ... FOR UPDATE SKIP LOCKED.
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
        Dispatches a claimed job to its registered handler.
        On success, marks COMPLETED.
        On failure, delegates to RetryService for retry scheduling or DLQ routing.
        """
        start_time = time.monotonic()
        attempt_number = job.attempt_count + 1

        # 1. Transition to RUNNING
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
            logger.info(
                "Worker %s executing Job %s (%s)", self.worker_id, job.id, job.job_type
            )

            handler = get_handler(job.job_type)
            if not handler:
                raise ValueError(
                    f"No handler registered for job_type '{job.job_type}'."
                )

            result_data = handler(job.payload)
            elapsed_ms = (time.monotonic() - start_time) * 1000

            # 2. Record SUCCESS and transition to COMPLETED
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
            stack_trace = traceback.format_exc()
            logger.exception(
                "Worker %s encountered error on Job %s", self.worker_id, job.id
            )

            attempt.status = AttemptStatus.FAILURE
            attempt.duration_ms = elapsed_ms
            attempt.error_message = str(exc)
            attempt.error_traceback = stack_trace
            attempt.completed_at = timezone.now()
            attempt.save()

            with transaction.atomic():
                RetryService.handle_failure(
                    job=job,
                    attempt=attempt,
                    exc=exc,
                    stack_trace=stack_trace,
                )

            return False

    def run(self) -> int:
        """
        Starts the worker polling loop.
        Returns the total number of jobs processed.
        """
        logger.info(
            "Worker %s started (poll_interval=%.2fs)",
            self.worker_id,
            self.options.poll_interval,
        )
        self.register_signal_handlers()
        jobs_processed = 0

        while not self.should_stop:
            self.send_heartbeat()
            job = self.claim_next_job(
                worker_id=self.worker_id,
                lease_duration_seconds=self.options.lease_duration_seconds,
            )

            if job:
                self.execute_job(job)
                jobs_processed += 1
            else:
                if self.options.burst:
                    logger.info(
                        "Worker %s burst complete: no more ready jobs.", self.worker_id
                    )
                    break
                time.sleep(self.options.poll_interval)

        logger.info(
            "Worker %s stopped. Total jobs processed: %d",
            self.worker_id,
            jobs_processed,
        )
        return jobs_processed
