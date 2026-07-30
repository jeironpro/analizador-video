from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, limit: int = 10, window: int = 60) -> None:
        self.limit = limit
        self.window = window
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = self._buckets.get(key, [])
            timestamps = [t for t in timestamps if now - t < self.window]
            if len(timestamps) >= self.limit:
                return False
            timestamps.append(now)
            self._buckets[key] = timestamps
            return True

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            self._buckets = {k: [t for t in ts if now - t < self.window] for k, ts in self._buckets.items()}
