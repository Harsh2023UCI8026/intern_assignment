from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque


class SlidingWindowRateLimiter:
    """A bounded, process-local sliding-window limiter for a single API worker."""

    def __init__(self, *, max_clients: int = 10_000) -> None:
        self._max_clients = max_clients
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(
        self, client_key: str, *, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            self._prune_expired(cutoff)
            timestamps = self._requests.get(client_key)
            if timestamps is None:
                timestamps = deque()
                self._requests[client_key] = timestamps
            else:
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()

            if len(timestamps) >= limit:
                self._requests.move_to_end(client_key)
                retry_after = max(
                    1, math.ceil(timestamps[0] + window_seconds - now)
                )
                return False, retry_after

            timestamps.append(now)
            self._requests.move_to_end(client_key)
            while len(self._requests) > self._max_clients:
                self._requests.popitem(last=False)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()

    def _prune_expired(self, cutoff: float) -> None:
        while self._requests:
            _, timestamps = next(iter(self._requests.items()))
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if timestamps:
                break
            self._requests.popitem(last=False)


chat_rate_limiter = SlidingWindowRateLimiter()
