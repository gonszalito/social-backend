import os

from redis.asyncio import Redis

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is not None:
        return _redis

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    socket_connect_timeout = float(os.environ.get("REDIS_SOCKET_CONNECT_TIMEOUT", "1.5"))
    socket_timeout = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "2.5"))
    _redis = Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=socket_connect_timeout,
        socket_timeout=socket_timeout,
    )
    return _redis

