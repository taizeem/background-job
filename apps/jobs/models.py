import uuid
from typing import ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.jobs.constants import AttemptStatus, JobPriority, JobStatus


class Job(models.Model):
    """
    Represents an asynchronous background job, its payload, execution lifecycle,
    and retry parameters.
    """

    # Allowed state machine transitions: current_status -> set of allowed next_statuses
    VALID_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        JobStatus.PENDING: {JobStatus.CLAIMED, JobStatus.CANCELLED},
        JobStatus.CLAIMED: {JobStatus.RUNNING, JobStatus.PENDING},
        JobStatus.RUNNING: {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.RETRYING,
            JobStatus.EXHAUSTED,
        },
        JobStatus.RETRYING: {JobStatus.CLAIMED, JobStatus.CANCELLED},
        # Terminal states have no valid subsequent transitions
        JobStatus.COMPLETED: set(),
        JobStatus.FAILED: set(),
        JobStatus.CANCELLED: set(),
        JobStatus.EXHAUSTED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(
        max_length=255,
        db_index=True,
        help_text="The registered handler identifier (e.g. reports.generate)",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON-serializable arguments passed to the job handler",
    )
    result = models.JSONField(
        null=True,
        blank=True,
        help_text="JSON-serializable output returned by a successful handler",
    )
    status = models.CharField(
        max_length=32,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    priority = models.IntegerField(
        choices=JobPriority.choices,
        default=JobPriority.DEFAULT,
        help_text="Higher values indicate higher execution priority",
    )

    # Retry and attempt configuration
    attempt_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of execution attempts initiated so far",
    )
    max_attempts = models.PositiveIntegerField(
        default=3,
        help_text="Maximum allowed attempts before moving to EXHAUSTED",
    )
    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when a RETRYING job becomes eligible for claiming",
    )

    # Worker lease and claiming state
    claimed_by = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="Unique identifier of the worker currently executing this job",
    )
    lease_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp after which an active lease is considered abandoned",
    )

    # Submission deduplication
    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Optional client-provided deduplication key",
    )

    # Error details from the most recent failure
    error_message = models.TextField(null=True, blank=True)
    error_traceback = models.TextField(null=True, blank=True)

    # Timestamps
    scheduled_at = models.DateTimeField(
        default=timezone.now,
        help_text="Earliest time this job is eligible for execution",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when current or last execution attempt began",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the job reached a terminal state",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-priority", "scheduled_at", "created_at")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(attempt_count__gte=0),
                name="chk_job_attempt_count_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(max_attempts__gte=1),
                name="chk_job_max_attempts_min_one",
            ),
            models.CheckConstraint(
                condition=models.Q(priority__gte=0),
                name="chk_job_priority_non_negative",
            ),
        )
        indexes = (
            # High-performance index for worker polling queries
            models.Index(
                fields=["status", "scheduled_at", "-priority", "created_at"],
                name="idx_job_polling",
            ),
            # Index for the Reaper finding zombie/abandoned running leases
            models.Index(
                fields=["status", "lease_expires_at"],
                name="idx_job_reaper",
            ),
        )

    def __str__(self) -> str:
        return f"Job {self.id} [{self.job_type}] - {self.status}"

    @property
    def is_terminal(self) -> bool:
        """Returns True if the job has completed, failed, cancelled, or exhausted."""
        return self.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXHAUSTED,
        }

    def can_transition_to(self, target_status: str) -> bool:
        """Determines if transitioning to target_status is permitted by the state graph."""
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        return target_status in allowed

    def transition_to(self, target_status: str) -> None:
        """
        Transitions the job to a new status if valid.
        Raises ValidationError if the transition violates the state graph.
        """
        if not self.can_transition_to(target_status):
            raise ValidationError(
                f"Illegal state transition from {self.status} to {target_status}."
            )
        self.status = target_status
        if self.is_terminal and not self.completed_at:
            self.completed_at = timezone.now()


class JobAttempt(models.Model):
    """
    Historical audit record of an individual execution attempt for a Job.
    Created every time a worker runs or attempts to run a job.
    """

    id = models.BigAutoField(primary_key=True)
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="attempts",
        help_text="The parent job associated with this execution attempt",
    )
    attempt_number = models.PositiveIntegerField()
    worker_id = models.CharField(
        max_length=128,
        help_text="Identifier of the worker process that ran this attempt",
    )
    status = models.CharField(
        max_length=32,
        choices=AttemptStatus.choices,
    )
    duration_ms = models.FloatField(
        null=True,
        blank=True,
        help_text="Total elapsed execution duration in milliseconds",
    )
    error_message = models.TextField(null=True, blank=True)
    error_traceback = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("attempt_number",)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(attempt_number__gte=1),
                name="chk_attempt_number_positive",
            ),
            models.UniqueConstraint(
                fields=["job", "attempt_number"],
                name="uq_job_attempt_number",
            ),
        )

    def __str__(self) -> str:
        return f"Attempt {self.attempt_number} for Job {self.job_id} ({self.status})"