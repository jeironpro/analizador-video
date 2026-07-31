from __future__ import annotations

import threading
import time

from services.redis_queue import get_redis, redis_available


class RateLimiter:
    """Límite de peticiones con ventana deslizante.

    Usa Redis si está disponible; si no, degrada a memoria del proceso
    (útil para entornos locales o en pruebas sin Redis).
    """

    def __init__(self, limit: int = 10, window: int = 60) -> None:
        self.limit = limit
        self.window = window
        self._key_prefix = f"ratelimit:{limit}:{window}"
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        if redis_available():
            return self._redis_is_allowed(key)
        return self._memory_is_allowed(key)

    def cleanup(self) -> None:
        if redis_available():
            try:
                keys = get_redis().keys(f"{self._key_prefix}:*")
                if keys:
                    get_redis().delete(*keys)
                return
            except Exception:
                pass
        now = time.time()
        with self._lock:
            self._buckets = {k: [t for t in ts if now - t < self.window] for k, ts in self._buckets.items()}

    def _redis_is_allowed(self, key: str) -> bool:
        redis = get_redis()
        rkey = f"{self._key_prefix}:{key}"
        now = time.time()
        score = now - self.window
        pipe = redis.pipeline(transaction=True)
        pipe.zremrangebyscore(rkey, "-inf", score)
        pipe.zadd(rkey, {str(now): now})
        pipe.zcard(rkey)
        pipe.expire(rkey, self.window * 2)
        _, _, count, _ = pipe.execute()
        if count > self.limit:
            redis.zrem(rkey, str(now))
            return False
        return True

    def _memory_is_allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = self._buckets.get(key, [])
            timestamps = [t for t in timestamps if now - t < self.window]
            if len(timestamps) >= self.limit:
                return False
            timestamps.append(now)
            self._buckets[key] = timestamps
            return True
