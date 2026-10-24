import logging
import signal
import time
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.jobs.services.job_service import JobService
from apps.scheduler.models import PeriodicTask
from apps.worker.reaper import ReaperService

logger = logging.getLogger(__name__)


@dataclass
class SchedulerOptions:
    """Configuration options for a SchedulerEngine instance."""

    poll_interval: float = 1.0
    burst: bool = False


class SchedulerEngine:
    """
    Dedicated scheduler process that discovers due PeriodicTasks and sweeps
    for expired worker leases.
    """

    def __init__(self, options: SchedulerOptions | None = None) -> None:
        self.options = options or SchedulerOptions()
        self.should_stop = False

    def register_signal_handlers(self) -> None:
        """Captures termination signals for clean process exit."""
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum: int, frame: Any) -> None:
        logger.info("Scheduler received shutdown signal (%s). Exiting...", signum)
        self.should_stop = True

    def enqueue_due_tasks(self) -> int:
        """
        Discovers all active PeriodicTasks where next_run_at <= NOW().
        Enqueues a Job for each task using an atomic transaction and deterministic idempotency key.
        """
        now = timezone.now()
        enqueued_count = 0

        with transaction.atomic():
            due_tasks = list(
                PeriodicTask.objects.select_for_update(skip_locked=True)
                .filter(is_active=True, next_run_at__lte=now)
                .order_by("next_run_at")
            )

            for task in due_tasks:
                scheduled_tick = task.next_run_at
                idempotency_key = f"periodic-{task.name}-{scheduled_tick.isoformat()}"

                JobService.create_job(
                    job_type=task.job_type,
                    payload=task.payload,
                    priority=task.priority,
                    scheduled_at=scheduled_tick,
                    idempotency_key=idempotency_key,
                )

                task.last_run_at = now
                task.total_run_count += 1
                task.next_run_at = task.calculate_next_run(from_time=now)
                task.save(
                    update_fields=[
                        "last_run_at",
                        "total_run_count",
                        "next_run_at",
                        "updated_at",
                    ]
                )
                enqueued_count += 1
                print(
                    f"[SCHEDULER] Enqueued periodic task '{task.name}' -> "
                    f"Next execution at {task.next_run_at.strftime('%H:%M:%S UTC')}"
                )
                logger.info(
                    "Scheduler enqueued task '%s' (next run at %s)",
                    task.name,
                    task.next_run_at.isoformat(),
                )

        return enqueued_count

    def run(self) -> int:
        """
        Starts the continuous scheduling and zombie lease reaping loop.
        """
        logger.info(
            "Scheduler engine started (poll_interval=%.2fs)",
            self.options.poll_interval,
        )
        self.register_signal_handlers()
        total_enqueued = 0

        while not self.should_stop:
            count = self.enqueue_due_tasks()
            ReaperService.reap_expired_leases()
            total_enqueued += count

            if self.options.burst:
                logger.info("Scheduler burst sweep finished. Enqueued %d tasks.", count)
                break

            time.sleep(self.options.poll_interval)

        logger.info("Scheduler engine stopped. Total jobs enqueued: %d", total_enqueued)
        return total_enqueued
