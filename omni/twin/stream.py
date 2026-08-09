"""Live Digital Twin observability stream (closes the M6b WebSocket item):
a real-time feed of twin snapshots and fleet events, so operators watch the
system evolve instead of polling `GET /twin/snapshot` in a loop.

`TwinBroadcaster` is transport-agnostic — it holds a set of sink callables
and fans a message out to all of them, pruning any sink that raises (its
connection is assumed dead). The FastAPI WebSocket route in
`omni/api/main.py` is a thin adapter: one sink per connected client,
bridged from the fleet bus's synchronous callbacks into the client's
asyncio queue via `loop.call_soon_threadsafe`, plus a periodic snapshot as
a heartbeat when nothing else happened.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

Sink = Callable[[dict[str, Any]], None]


class TwinBroadcaster:
    """Process-local fan-out. One instance is shared by every connected
    stream client; `broadcast()` is called from whichever thread produced
    the event (an HTTP request handler, a fleet bus callback, ...)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sinks: dict[int, Sink] = {}
        self._next_id = 0

    def subscribe(self, sink: Sink) -> int:
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._sinks[sub_id] = sink
            return sub_id

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._sinks.pop(sub_id, None)

    def broadcast(self, message: dict[str, Any]) -> int:
        """Fan a message out to every connected sink; returns delivery
        count. A sink that raises is dropped rather than breaking delivery
        to the rest (its connection is assumed to have died)."""
        with self._lock:
            sinks = list(self._sinks.items())
        delivered = 0
        dead: list[int] = []
        for sub_id, sink in sinks:
            try:
                sink(message)
                delivered += 1
            except Exception:
                dead.append(sub_id)
        if dead:
            with self._lock:
                for sub_id in dead:
                    self._sinks.pop(sub_id, None)
        return delivered

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._sinks)
