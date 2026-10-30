"""Redis helpers: cached aggregate stats and a simple fixed-window rate limiter.
Redis is optional at runtime; every failure falls back to MongoDB / allows the request."""
import json
import logging

import redis
from django.conf import settings

from . import database

logger = logging.getLogger(__name__)
STATS_KEY = "pickmicro:stats"
_client = None


def get_client():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
        )
    return _client


def ping():
    try:
        return bool(get_client().ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis ping failed: %s", exc)
        return False


def get_stats():
    """Return aggregate stats dict with a 'cached' flag. Raises DatabaseUnavailable if MongoDB is down
    and nothing is cached."""
    try:
        cached = get_client().get(STATS_KEY)
        if cached:
            stats = json.loads(cached)
            stats["cached"] = True
            return stats
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis read failed, using MongoDB: %s", exc)
    stats = database.aggregate_stats()
    try:
        get_client().set(STATS_KEY, json.dumps(stats), ex=settings.STATS_CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis write failed: %s", exc)
    stats["cached"] = False
    return stats


def invalidate_stats():
    try:
        get_client().delete(STATS_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis invalidate failed: %s", exc)


def allow_request(identifier):
    """Fixed-window limiter. Fails open if Redis is unavailable."""
    limit, window = settings.RATE_LIMIT_REQUESTS, settings.RATE_LIMIT_WINDOW_SECONDS
    if limit <= 0:
        return True
    key = f"pickmicro:rl:{identifier}"
    try:
        pipe = get_client().pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if ttl == -1:
            get_client().expire(key, window)
        return count <= limit
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rate limiter unavailable, allowing request: %s", exc)
        return True
