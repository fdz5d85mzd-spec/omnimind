"""Background heartbeat for the standing fleet: every interval, rebalance
queued work across agents and publish a real tick to the fleet bus (visible
live in Mission Control) — an actual scheduler doing actual work, not a
fake "online" indicator.
"""

from __future__ import annotations

import asyncio
import logging
import time

from omni.fleet.bus import FleetBus
from omni.orchestrator.engine import MetaOrchestrator

logger = logging.getLogger(__name__)


class FleetScheduler:
    def __init__(self, orchestrator: MetaOrchestrator, bus: FleetBus, interval_s: float = 60.0) -> None:
        self._orchestrator = orchestrator
        self._bus = bus
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None

    def tick(self) -> dict:
        """One scheduling cycle: rebalance, then report — always safe to
        call directly (e.g. from a test) without the asyncio loop."""
        moves = self._orchestrator.balance()
        report = self._orchestrator.report()
        payload = {
            "moved": len(moves),
            "report": report,
            "at": time.time(),
        }
        self._bus.publish("fleet.scheduler.tick", payload)
        return payload

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_s)
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive, never crash the loop
                logger.exception("fleet scheduler tick failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
