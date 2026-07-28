"""Login throttling.

Implemented directly rather than via a decorator: slowapi's decorator rebinds
the endpoint into its own module namespace, and because this project uses
``from __future__ import annotations`` FastAPI can then no longer resolve the
endpoint's dependency annotations. An explicit throttle is also easier to test.

This is in-process and per-worker. It raises the cost of online password
guessing; it is not a substitute for rate limiting at the reverse proxy.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from typing import Final

_PERIODS: Final[dict[str, int]] = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}
_LIMIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d+)\s*/\s*(second|minute|hour|day)\s*$", re.IGNORECASE
)


def parse_limit(limit: str) -> tuple[int, int]:
    """Parse ``"5/minute"`` into ``(5, 60)``."""
    match = _LIMIT_PATTERN.match(limit)
    if match is None:
        raise ValueError(f"invalid rate limit {limit!r}; expected e.g. '5/minute'")
    return int(match.group(1)), _PERIODS[match.group(2).lower()]


class RateLimiter:
    """Fixed-quota sliding-window counter keyed by client identifier."""

    def __init__(self, limit: str) -> None:
        self._max_events, self._window = parse_limit(limit)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record an attempt and report whether it is within the quota."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._events[key]
            cutoff = moment - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_events:
                return False
            bucket.append(moment)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


def client_key(client_host: str | None) -> str:
    return client_host or "unknown"
