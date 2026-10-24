"""
Queue depth and platform telemetry metrics service with Redis caching.
"""

import json
import logging
from typing import Any

from django.db.models import Avg, Count
from django.utils import timezone
from redis.exceptions import RedisError

from apps.core.redis import get_redis_client, is_redis_available
from apps.jobs.constants import AttemptStatus, JobStatus
from apps.jobs.models import Job, JobAttempt

logger = logging.getLogger(__name__)


class MetricsService:
    """Computes and caches platform queue depths, worker health, and execution telemetry."""

    CACHE_KEY = "telemetry:queue_metrics"
    CACHE_TTL_SECONDS = 5

    @classmethod
    def get_active_workers_count(cls) -> int:
        """Counts active worker heartbeat keys in Redis."""
        if not is_redis_available():
            return 0
        try:
            client = get_redis_client()
            keys = client.keys("worker:heartbeat:*")
            return len(keys) if isinstance(keys, list) else 0
        except (RedisError, OSError) as exc:
            logger.warning("Failed to count active worker heartbeats: %s", exc)
            return 0

    @classmethod
    def get_queue_metrics(cls) -> dict[str, Any]:
        """
        Returns live queue counts, attempt statistics, and active worker counts.
        Checks Redis cache first; on cache miss, queries PostgreSQL and populates cache.
        """
        if is_redis_available():
            try:
                client = get_redis_client()
                cached_data = client.get(cls.CACHE_KEY)
                if cached_data:
                    data = json.loads(cached_data)
                    data["source"] = "redis_cache"
                    return data
            except (RedisError, OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read metrics from Redis: %s", exc)

        # 1. Queue depth counts by job status
        status_counts = dict(
            Job.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )

        # 2. Execution attempt statistics and average duration
        attempt_counts = dict(
            JobAttempt.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )
        avg_duration = JobAttempt.objects.filter(duration_ms__isnull=False).aggregate(
            avg_ms=Avg("duration_ms")
        )["avg_ms"]

        metrics = {
            "source": "postgresql_live",
            "timestamp": timezone.now().isoformat(),
            "active_workers": cls.get_active_workers_count(),
            "queue_depths": {
                "pending": status_counts.get(JobStatus.PENDING, 0),
                "claimed": status_counts.get(JobStatus.CLAIMED, 0),
                "running": status_counts.get(JobStatus.RUNNING, 0),
                "retrying": status_counts.get(JobStatus.RETRYING, 0),
                "completed": status_counts.get(JobStatus.COMPLETED, 0),
                "failed": status_counts.get(JobStatus.FAILED, 0),
                "exhausted": status_counts.get(JobStatus.EXHAUSTED, 0),
                "cancelled": status_counts.get(JobStatus.CANCELLED, 0),
            },
            "attempt_metrics": {
                "success": attempt_counts.get(AttemptStatus.SUCCESS, 0),
                "failure": attempt_counts.get(AttemptStatus.FAILURE, 0),
                "abandoned": attempt_counts.get(AttemptStatus.ABANDONED, 0),
                "avg_duration_ms": round(float(avg_duration), 2)
                if avg_duration is not None
                else 0.0,
            },
            "total_jobs": sum(status_counts.values()),
        }

        if is_redis_available():
            try:
                client = get_redis_client()
                client.setex(cls.CACHE_KEY, cls.CACHE_TTL_SECONDS, json.dumps(metrics))
            except (RedisError, OSError) as exc:
                logger.warning("Failed to write metrics to Redis: %s", exc)

        return metrics
