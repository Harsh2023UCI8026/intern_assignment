from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class CachedResponse:
    reply: str
    input_tokens: int
    output_tokens: int


class ExactResponseCache:
    """Bounded process-local cache for exact model + conversation requests."""

    def __init__(self, *, max_entries: int = 256, ttl_seconds: int = 3600) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, CachedResponse]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedResponse | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, response = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return response

    def put(self, key: str, response: CachedResponse) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self._ttl_seconds, response)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def exact_request_key(model: str, messages: list[dict[str, str]]) -> str:
    request = json.dumps(
        {"model": model, "messages": messages},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(request.encode("utf-8")).hexdigest()


response_cache = ExactResponseCache()
