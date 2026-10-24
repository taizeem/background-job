"""
Zombie lease reaper service for recovering jobs abandoned by crashed workers.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.jobs.constants import AttemptStatus, JobStatus
from apps.jobs.models import Job, JobAttempt

logger = logging.getLogger(__name__)


class ReaperService:
    """
    Detects and recovers jobs held by workers whose leases have expired.
    """

    @classmethod
    def reap_expired_leases(cls) -> int:
        """
        Finds all jobs in CLAIMED or RUNNING state where lease_expires_at < NOW().
        Recovers them to RETRYING if attempts remain, or EXHAUSTED if retry budget is spent.
        Returns the total number of recovered zombie jobs.
        """
        now = timezone.now()
        reaped_count = 0

        with transaction.atomic():
            zombie_jobs = list(
                Job.objects.select_for_update(skip_locked=True)
                .filter(
                    status__in=(JobStatus.CLAIMED, JobStatus.RUNNING),
                    lease_expires_at__lt=now,
                )
                .order_by("lease_expires_at")
            )

            for job in zombie_jobs:
                dead_worker = job.claimed_by or "unknown-worker"
                # If the worker crashed in CLAIMED before incrementing attempt_count,
                # increment it now so crashed claims consume retry budget and can't loop forever.
                if job.status == JobStatus.CLAIMED:
                    job.attempt_count += 1

                attempt_num = job.attempt_count
                abandon_reason = (
                    f"Lease expired at {job.lease_expires_at.isoformat()} "
                    f"(worker '{dead_worker}' crashed or became unresponsive)."
                )

                # Record or update the audit attempt as ABANDONED
                JobAttempt.objects.update_or_create(
                    job=job,
                    attempt_number=attempt_num,
                    defaults={
                        "worker_id": dead_worker,
                        "status": AttemptStatus.ABANDONED,
                        "error_message": abandon_reason,
                        "completed_at": now,
                    },
                )

                # Determine whether to re-queue or move to Dead-Letter Queue
                if job.attempt_count < job.max_attempts:
                    job.transition_to(JobStatus.RETRYING)
                    job.error_message = abandon_reason
                    job.claimed_by = None
                    job.lease_expires_at = None
                    job.next_retry_at = now
                    job.scheduled_at = now
                    job.save(
                        update_fields=[
                            "status",
                            "attempt_count",
                            "error_message",
                            "claimed_by",
                            "lease_expires_at",
                            "next_retry_at",
                            "scheduled_at",
                            "updated_at",
                        ]
                    )
                    logger.warning(
                        "Reaper recovered zombie Job %s from worker '%s' -> RETRYING (attempt %d/%d)",
                        job.id,
                        dead_worker,
                        job.attempt_count,
                        job.max_attempts,
                    )
                else:
                    job.transition_to(JobStatus.EXHAUSTED)
                    job.error_message = (
                        f"Max attempts ({job.max_attempts}) exhausted after worker crash. "
                        f"{abandon_reason}"
                    )
                    job.claimed_by = None
                    job.lease_expires_at = None
                    job.completed_at = now
                    job.save(
                        update_fields=[
                            "status",
                            "attempt_count",
                            "error_message",
                            "claimed_by",
                            "lease_expires_at",
                            "completed_at",
                            "updated_at",
                        ]
                    )
                    logger.error(
                        "Reaper moved zombie Job %s from worker '%s' -> EXHAUSTED (attempts %d/%d)",
                        job.id,
                        dead_worker,
                        job.attempt_count,
                        job.max_attempts,
                    )

                reaped_count += 1

        return reaped_count
