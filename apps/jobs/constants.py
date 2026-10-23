from django.db import models


class JobStatus(models.TextChoices):
    """Lifecycle states for a background job."""

    PENDING = "PENDING", "Pending"
    CLAIMED = "CLAIMED", "Claimed"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    RETRYING = "RETRYING", "Retrying"
    CANCELLED = "CANCELLED", "Cancelled"
    EXHAUSTED = "EXHAUSTED", "Exhausted"


class JobPriority(models.IntegerChoices):
    """Execution priority levels. Higher numbers take precedence."""

    LOW = 10, "Low"
    DEFAULT = 20, "Default"
    HIGH = 30, "High"
    CRITICAL = 40, "Critical"


class AttemptStatus(models.TextChoices):
    """Outcome status for an individual job execution attempt."""

    SUCCESS = "SUCCESS", "Success"
    FAILURE = "FAILURE", "Failure"
    TIMEOUT = "TIMEOUT", "Timeout"
    ABANDONED = "ABANDONED", "Abandoned"
