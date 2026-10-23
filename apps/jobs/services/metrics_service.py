"""
Queue depth and platform telemetry metrics service with Redis caching.
"""

import json
import logging
from typing import Any

from django.db.models import Count
from django.utils import timezone
from redis.exceptions import RedisError

from apps.core.redis import get_redis_client, is_redis_available
from apps.jobs.constants import JobStatus
from apps.jobs.models import Job

logger = logging.getLogger(__name__)


class MetricsService:
    """Computes and caches platform queue depths and operational metrics."""

    CACHE_KEY = "telemetry:queue_metrics"
    CACHE_TTL_SECONDS = 5

    @classmethod
    def get_queue_metrics(cls) -> dict[str, Any]:
        """
        Returns live queue counts. Checks Redis cache first;
        on cache miss, queries PostgreSQL and populates cache.
        """
        # 1. Attempt to read from Redis cache
        if is_redis_available():
            try:
                client = get_redis_client()
                cached_data = client.get(cls.CACHE_KEY)
                if cached_data:
                    data = json.loads(cached_data)
                    data["source"] = "redis_cache"
                    return data
            except (RedisError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read metrics from Redis: %s", exc)

        # 2. Cache miss or Redis unavailable: query PostgreSQL
        status_counts = dict(
            Job.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )

        metrics = {
            "source": "postgresql_live",
            "timestamp": timezone.now().isoformat(),
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
            "total_jobs": sum(status_counts.values()),
        }

        # 3. Populate Redis cache with 5s TTL
        if is_redis_available():
            try:
                client = get_redis_client()
                client.setex(cls.CACHE_KEY, cls.CACHE_TTL_SECONDS, json.dumps(metrics))
            except RedisError as exc:
                logger.warning("Failed to write metrics to Redis: %s", exc)

        return metrics
