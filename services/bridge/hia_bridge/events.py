"""Bounded in-memory event delivery for authenticated long polling."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class EventBuffer:
    """A transient event ring; it never writes chat content to disk."""

    def __init__(self, max_events: int = 2048) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._condition = threading.Condition()
        self._next_sequence = 1

    def publish(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._condition:
            event = {
                "seq": self._next_sequence,
                "timestamp": time.time(),
                "type": event_type,
                **fields,
            }
            self._next_sequence += 1
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    def poll(
        self,
        after: int,
        *,
        timeout: float = 15.0,
        limit: int = 256,
    ) -> dict[str, Any]:
        if after < 0:
            raise ValueError("after must be non-negative")
        timeout = max(0.0, min(timeout, 25.0))
        limit = max(1, min(limit, 512))
        deadline = time.monotonic() + timeout

        with self._condition:
            while not any(event["seq"] > after for event in self._events):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)

            events = [
                dict(event) for event in self._events if event["seq"] > after
            ][:limit]
            first_available = self._events[0]["seq"] if self._events else self._next_sequence
            gap = bool(after and after < first_available - 1)
            latest = events[-1]["seq"] if events else after
            return {
                "events": events,
                "latest": latest,
                "gap": gap,
            }
