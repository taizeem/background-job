from django.db import models

# Create your models here.
import uuid
from datetime import datetime, timedelta

from croniter import croniter
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.jobs.constants import JobPriority


class PeriodicTask(models.Model):
    """
    Defines a recurring background task executed on a recurring interval
    or standard cron schedule.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=128,
        unique=True,
        help_text="Unique human-readable identifier (e.g. hourly-cache-cleanup)",
    )
    job_type = models.CharField(
        max_length=255,
        help_text="The registered handler identifier (e.g. maintenance.cleanup)",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON payload passed to each generated job instance",
    )
    priority = models.IntegerField(
        choices=JobPriority.choices,
        default=JobPriority.DEFAULT,
    )

    # Schedule definitions: cron or interval
    cron_expression = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Standard 5-part cron syntax (e.g. '0 * * * *' for hourly)",
    )
    interval_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Recurring interval in seconds (e.g. 300 for every 5 minutes)",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Inactive tasks are ignored by the scheduler",
    )
    total_run_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this periodic task has been enqueued",
    )
    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent execution dispatch",
    )
    next_run_at = models.DateTimeField(
        db_index=True,
        help_text="Next scheduled execution timestamp",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("next_run_at", "name")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(cron_expression__isnull=False)
                    | models.Q(interval_seconds__isnull=False)
                ),
                name="chk_periodic_has_schedule",
            ),
        )
        indexes = (
            models.Index(
                fields=["is_active", "next_run_at"],
                name="idx_periodic_due_tasks",
            ),
        )

    def clean(self) -> None:
        """Validates that exactly one schedule type is provided and parses cron syntax."""
        if not self.cron_expression and not self.interval_seconds:
            raise ValidationError("You must specify either a cron_expression or interval_seconds.")

        if self.cron_expression and self.interval_seconds:
            raise ValidationError("Cannot specify both cron_expression and interval_seconds.")

        if self.cron_expression and not croniter.is_valid(self.cron_expression):
            raise ValidationError(f"Invalid cron expression: '{self.cron_expression}'.")

    def save(self, *args, **kwargs) -> None:
        self.clean()
        if not self.next_run_at:
            self.next_run_at = self.calculate_next_run(from_time=timezone.now())
        super().save(*args, **kwargs)

    def calculate_next_run(self, from_time: datetime | None = None) -> datetime:
        """
        Calculates the next execution timestamp based on the schedule definition.
        """
        base_time = from_time or timezone.now()

        if self.interval_seconds:
            return base_time + timedelta(seconds=self.interval_seconds)

        if self.cron_expression:
            iter_cron = croniter(self.cron_expression, base_time)
            next_timestamp = iter_cron.get_next(datetime)
            # Ensure timezone-aware datetime
            if timezone.is_naive(next_timestamp):
                next_timestamp = timezone.make_aware(next_timestamp, timezone.utc)
            return next_timestamp

        raise ValidationError("No valid schedule configured on PeriodicTask.")

    def __str__(self) -> str:
        schedule = self.cron_expression or f"every {self.interval_seconds}s"
        return f"PeriodicTask {self.name} ({schedule}) -> {self.job_type}"