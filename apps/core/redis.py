"""
Redis client connection factory and utility functions.
"""

import logging

import redis
from django.conf import settings
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None


def get_redis_pool() -> redis.ConnectionPool:
    """Returns a shared thread-safe Redis connection pool."""
    global _pool
    if _pool is None:
        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        _pool = redis.ConnectionPool.from_url(
            redis_url,
            max_connections=20,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            decode_responses=True,
        )
    return _pool


def get_redis_client() -> redis.Redis:
    """Returns a Redis client using the shared pool."""
    return redis.Redis(connection_pool=get_redis_pool())


def is_redis_available() -> bool:
    """Returns True if Redis is responding to PING requests."""
    try:
        client = get_redis_client()
        return client.ping() is True
    except RedisError as exc:
        logger.warning("Redis health check failed: %s", exc)
        return False
