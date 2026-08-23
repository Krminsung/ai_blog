"""Lazy asynchronous Redis connection."""

from functools import lru_cache

from redis.asyncio import Redis

from blogops.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_health_timeout_seconds,
        socket_timeout=settings.redis_health_timeout_seconds,
    )


async def close_redis() -> None:
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()
