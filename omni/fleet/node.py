"""FleetNode — a member of a distributed OmniMind fleet (M8).

Responsibilities
----------------
* announce itself into the shared registry (heartbeat payload with health),
* participate in deterministic leader election (highest capacity wins;
  ties broken by node id — stable across restarts),
* expose workload stats derived from a local MetaOrchestrator,
* enqueue global tasks and adopt (lease) queued tasks from the shared queue.

The layer between a node and its peers is the `FleetStorage` abstraction:
`PostgresFleetStorage` for production, `InMemoryFleetStorage` for tests.
"""

from __future__ import annotations

from typing import Any

from omni.fleet.bus import FleetBus
from omni.fleet.protocol import NodeAnnouncement, NodeHealth, WorkloadStats, node_id
from omni.fleet.storage import FleetStorage


class FleetNode:
    def __init__(
        self,
        storage: FleetStorage,
        node_id_value: str | None = None,
        capacity: int = 32,
        version: str = "0.4.0",
        bus: FleetBus | None = None,
    ) -> None:
        self.storage = storage
        self.id = node_id_value or node_id()
        self.capacity = capacity
        self.version = version
        self.bus = bus  # optional real-time event bus (InMemoryBus / NatsBus); None = polling-only
        self._announcement_key = f"fleet.node.{self.id}"

    def _publish(self, subject: str, payload: dict[str, Any]) -> None:
        if self.bus is not None:
            self.bus.publish(subject, payload)

    # ------------------------------------------------------------ registry
    def announce(self, health: NodeHealth | None = None) -> dict[str, Any]:
        announcement = NodeAnnouncement(
            node=self.id,
            version=self.version,
            capacity=self.capacity,
            health=health,
        )
        result = self.storage.announce(self._announcement_key, announcement.model_dump(mode="json"))
        self._publish(f"fleet.{self.id}.announce", result)
        return result

    def peers(self) -> list[dict[str, Any]]:
        return [n for n in self.storage.nodes() if n.get("node") != self.id]

    def is_registered(self) -> bool:
        return any(n.get("node") == self.id for n in self.storage.nodes())

    # ------------------------------------------------------------ election
    def elect(self) -> dict[str, Any]:
        """Deterministic election: highest capacity wins, node id breaks ties.
        Re-announces self first so the node is part of the candidate set.
        Only the winner persists the new term; losers return the consensus
        view (winner + stable term) so the result is computable from any node."""
        self.announce()
        candidates = self.storage.nodes()
        if not candidates:
            return {"leader": None, "term": 0}
        best = max(candidates, key=lambda n: (int(n.get("capacity", 0)), n.get("node", "")))
        current = self.storage.get_leader()
        if best["node"] == self.id:
            term = (current["term"] + 1) if current else 1
            result = self.storage.set_leader(self.id, term)
            self._publish("fleet.leader.elected", result)
            return result
        term = current["term"] if current and current["leader"] == best["node"] else 0
        return {"leader": best["node"], "term": term}

    def is_leader(self) -> bool:
        leader = self.storage.get_leader()
        return leader is not None and leader["leader"] == self.id

    # ------------------------------------------------------------ workload
    def workload(self, orchestrator) -> WorkloadStats:
        report = orchestrator.report()
        return WorkloadStats(
            total_agents=report["agents_total"],
            total_queued=report["tasks_queued"],
            average_load=report["avg_load"],
            total_cost=orchestrator.total_cost(),
            arrival_rate=orchestrator.arrival_rate(),
        )

    # ------------------------------------------------------------ tasks
    def enqueue(self, task: dict[str, Any]) -> dict[str, Any]:
        result = self.storage.enqueue(task)
        self._publish("fleet.queue.enqueued", result)
        return result

    def adopt_task(self) -> dict[str, Any] | None:
        """Lease one global task for this node to execute."""
        task = self.storage.lease_task(self.id)
        if task is not None:
            self._publish("fleet.queue.leased", {"node": self.id, **task})
        return task
