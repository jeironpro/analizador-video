from __future__ import annotations

from typing import Any

import redis
from rq import Queue

from services.config import REDIS_URL, RQ_QUEUE

_client: Any | None = None
_available: bool | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            REDIS_URL,
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=30,
        )
    return _client


def redis_available() -> bool:
    """Comprueba una vez si Redis responde (con timeout corto) y cachea el resultado."""
    global _available
    if _available is None:
        try:
            probe = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
            _available = bool(probe.ping())
        except Exception:
            _available = False
    return _available


def get_rq_queue(name: str | None = None) -> Queue:
    return Queue(name or RQ_QUEUE, connection=get_redis())
