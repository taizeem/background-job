"""
Sliding-window rate limiter powered by Redis.
"""

import logging
import time
from typing import Any

from redis.exceptions import RedisError
from rest_framework import throttling
from rest_framework.request import Request

from apps.core.redis import get_redis_client

logger = logging.getLogger(__name__)


class RedisRateThrottle(throttling.BaseThrottle):
    """
    Rolling sliding-window rate limiter.
    Limits requests to 'rate_limit' requests per 'window_seconds' per API key.
    """

    rate_limit: int = 60
    window_seconds: int = 60

    def __init__(self, rate_limit: int | None = None, window_seconds: int | None = None):
        if rate_limit:
            self.rate_limit = rate_limit
        if window_seconds:
            self.window_seconds = window_seconds
        self.retry_after: int = 0

    def get_ident(self, request: Request) -> str:
        """Derives a unique rate limit identifier from API key or client IP."""
        api_key = getattr(request, "auth", None)
        if api_key and hasattr(api_key, "id"):
            return f"user:{request.user.id}:key:{api_key.id}"
        return f"ip:{request.META.get('REMOTE_ADDR', '127.0.0.1')}"

    def allow_request(self, request: Request, view: Any) -> bool:
        identifier = self.get_ident(request)
        redis_key = f"ratelimit:{identifier}"
        now = time.time()
        window_start = now - self.window_seconds

        try:
            client = get_redis_client()
            pipe = client.pipeline()

            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {str(now): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, self.window_seconds + 5)

            results = pipe.execute()
            request_count = results[2]

            if request_count > self.rate_limit:
                oldest = client.zrange(redis_key, 0, 0, withscores=True)
                if oldest:
                    oldest_time = oldest[0][1]
                    self.retry_after = max(1, int(oldest_time + self.window_seconds - now))
                else:
                    self.retry_after = self.window_seconds

                logger.warning(
                    "Rate limit exceeded for %s (%d/%d req in %ds). Retry-After: %ds",
                    identifier,
                    request_count,
                    self.rate_limit,
                    self.window_seconds,
                    self.retry_after,
                )
                return False

            return True

        except (RedisError, OSError) as exc:
            logger.warning(
                "Redis rate limiter unavailable (%s). Failing open for %s.",
                exc,
                identifier,
            )
            return True

    def wait(self) -> int:
        return self.retry_after