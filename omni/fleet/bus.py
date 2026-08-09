"""Fleet message bus (M8+): real-time pub/sub for fleet coordination events.

`FleetStorage` (storage.py) is the durable, poll-based source of truth —
nodes call `announce()` / `nodes()` / `get_leader()` whenever they choose to
look. `FleetBus` is the complementary push channel: when something happens
(a node announces, a leader is elected, a task is queued or leased), an
event is published immediately so subscribers (peer nodes, the Digital Twin,
a future WebSocket stream) don't have to poll to find out.

Two implementations, same interface:

* `InMemoryBus` — synchronous, in-process, zero dependencies. The default,
  and what the test suite uses; correct for a single-process deployment.
* `NatsBus` — backed by a real NATS server (`nats-py`, imported lazily, like
  `psycopg` for `PostgresFleetStorage`) for multi-process / multi-node fleets.
  Bridges the async `nats-py` client into this codebase's synchronous API by
  running a dedicated asyncio event loop on a background thread.

Subject convention (NATS-style dot-separated, `>` matches all suffixes):

* `fleet.<node_id>.announce` — a node posted a health/heartbeat announcement
* `fleet.leader.elected`     — a new leader was elected (payload: LeaderInfo)
* `fleet.queue.enqueued`     — a task was added to the shared queue
* `fleet.queue.leased`       — a task was leased by a node
"""

from __future__ import annotations

import abc
import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Callable

EventHandler = Callable[[str, dict[str, Any]], None]


@dataclass
class Subscription:
    """Handle returned by `subscribe()`; call `.unsubscribe()` to stop delivery."""

    subject: str
    _bus: "FleetBus"
    _id: int

    def unsubscribe(self) -> None:
        self._bus._unsubscribe(self.subject, self._id)


class FleetBus(abc.ABC):
    """Interface used by FleetNode (and future observability consumers) for
    real-time fleet events. Delivery is best-effort — the bus is an
    optimization over polling, never the sole source of truth."""

    @abc.abstractmethod
    def publish(self, subject: str, payload: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def subscribe(self, subject: str, handler: EventHandler) -> Subscription: ...

    @abc.abstractmethod
    def _unsubscribe(self, subject: str, sub_id: int) -> None: ...

    def close(self) -> None:  # pragma: no cover - no-op unless overridden
        pass


def _matches(pattern: str, subject: str) -> bool:
    """NATS-style subject matching: '*' matches one token, '>' matches the
    rest of the subject (must be the final token in the pattern)."""
    if pattern == subject:
        return True
    p_parts = pattern.split(".")
    s_parts = subject.split(".")
    for i, p in enumerate(p_parts):
        if p == ">":
            return True  # matches this token and everything after
        if i >= len(s_parts):
            return False
        if p != "*" and p != s_parts[i]:
            return False
    return len(p_parts) == len(s_parts)


class InMemoryBus(FleetBus):
    """Synchronous, process-local pub/sub. Handlers run inline on the
    publishing thread/call — deterministic, no background threads, safe
    for tests and single-process deployments."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, list[tuple[int, EventHandler]]] = {}
        self._next_id = 0
        self.published: list[tuple[str, dict[str, Any]]] = []  # audit trail, useful in tests

    def publish(self, subject: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.published.append((subject, payload))
            handlers: list[EventHandler] = []
            for pattern, subs in self._subs.items():
                if _matches(pattern, subject):
                    handlers.extend(h for _, h in subs)
        for handler in handlers:
            handler(subject, payload)

    def subscribe(self, subject: str, handler: EventHandler) -> Subscription:
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs.setdefault(subject, []).append((sub_id, handler))
        return Subscription(subject=subject, _bus=self, _id=sub_id)

    def _unsubscribe(self, subject: str, sub_id: int) -> None:
        with self._lock:
            subs = self._subs.get(subject, [])
            self._subs[subject] = [(i, h) for i, h in subs if i != sub_id]


class NatsBus(FleetBus):
    """Production adapter backed by a real NATS server. Requires the
    `nats-py` package (`pip install nats-py`) and a reachable server —
    both optional, imported/connected lazily so the rest of the platform
    never pays for this dependency unless NATS is actually configured.

    `nats-py` is asyncio-only; this class runs one event loop on a
    dedicated background thread and marshals publish/subscribe calls onto
    it with `asyncio.run_coroutine_threadsafe`, so callers use it exactly
    like `InMemoryBus` — no `await` anywhere in this codebase's sync API.
    """

    def __init__(self, url: str = "nats://localhost:4222", connect_timeout_s: float = 5.0) -> None:
        import nats  # imported lazily: package is optional

        self._nats = nats
        self._url = url
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._conn = self._run(nats.connect(url), timeout=connect_timeout_s)
        self._subs: dict[str, Any] = {}  # subject -> nats subscription object

    def _run(self, coro, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def publish(self, subject: str, payload: dict[str, Any]) -> None:
        import json

        self._run(self._conn.publish(subject, json.dumps(payload).encode("utf-8")))

    def subscribe(self, subject: str, handler: EventHandler) -> Subscription:
        import json

        async def _on_msg(msg) -> None:
            handler(msg.subject, json.loads(msg.data.decode("utf-8")))

        nats_subject = subject.replace("*", "*").replace(">", ">")  # NATS wildcards match ours 1:1
        sub = self._run(self._conn.subscribe(nats_subject, cb=_on_msg))
        sub_id = id(sub)
        self._subs[sub_id] = sub
        return Subscription(subject=subject, _bus=self, _id=sub_id)

    def _unsubscribe(self, subject: str, sub_id: int) -> None:
        sub = self._subs.pop(sub_id, None)
        if sub is not None:
            self._run(sub.unsubscribe())

    def close(self) -> None:
        if self._conn is not None:
            self._run(self._conn.drain(), timeout=5.0)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)


def make_bus(url: str | None) -> FleetBus:
    """Factory: NatsBus if a NATS URL is configured (env `NATS_URL`), else
    the zero-dependency InMemoryBus. Used by the API/CLI so the platform
    runs out of the box and upgrades transparently when NATS is available."""
    if url:
        return NatsBus(url)
    return InMemoryBus()
