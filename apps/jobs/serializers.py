from rest_framework import serializers

from apps.jobs.constants import JobPriority
from apps.jobs.models import Job, JobAttempt
from apps.jobs.registry import is_job_type_registered


class JobAttemptSerializer(serializers.ModelSerializer):
    """Serializes execution attempt audit logs."""

    class Meta:
        model = JobAttempt
        fields = (
            "id",
            "attempt_number",
            "worker_id",
            "status",
            "duration_ms",
            "error_message",
            "started_at",
            "completed_at",
            "created_at",
        )
        read_only_fields = fields


class JobListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing jobs."""

    class Meta:
        model = Job
        fields = (
            "id",
            "job_type",
            "status",
            "priority",
            "attempt_count",
            "max_attempts",
            "scheduled_at",
            "created_at",
            "completed_at",
        )
        read_only_fields = fields


class JobDetailSerializer(serializers.ModelSerializer):
    """Detailed representation of a Job, including payload, results, and attempts."""

    attempts = JobAttemptSerializer(many=True, read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "job_type",
            "payload",
            "result",
            "status",
            "priority",
            "attempt_count",
            "max_attempts",
            "idempotency_key",
            "error_message",
            "error_traceback",
            "scheduled_at",
            "started_at",
            "completed_at",
            "next_retry_at",
            "created_at",
            "updated_at",
            "attempts",
        )
        read_only_fields = fields


class JobCreateSerializer(serializers.Serializer):
    """Validates incoming job submission requests."""

    job_type = serializers.CharField(max_length=255)
    payload = serializers.DictField(default=dict)
    priority = serializers.ChoiceField(
        choices=JobPriority.choices,
        default=JobPriority.DEFAULT,
    )
    max_attempts = serializers.IntegerField(
        default=3,
        min_value=1,
        max_value=10,
    )
    scheduled_at = serializers.DateTimeField(required=False)
    idempotency_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_null=True,
        allow_blank=False,
    )

    def validate_job_type(self, value: str) -> str:
        """Ensures the submitted job_type is recognized by the platform registry."""
        if not is_job_type_registered(value):
            raise serializers.ValidationError(
                f"Unsupported job_type '{value}'. Must be one of registered types."
            )
        return value
