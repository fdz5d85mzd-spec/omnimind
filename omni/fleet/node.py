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

from omni.fleet.protocol import NodeAnnouncement, NodeHealth, WorkloadStats, node_id
from omni.fleet.storage import FleetStorage


class FleetNode:
    def __init__(
        self,
        storage: FleetStorage,
        node_id_value: str | None = None,
        capacity: int = 32,
        version: str = "0.4.0",
    ) -> None:
        self.storage = storage
        self.id = node_id_value or node_id()
        self.capacity = capacity
        self.version = version
        self._announcement_key = f"fleet.node.{self.id}"

    # ------------------------------------------------------------ registry
    def announce(self, health: NodeHealth | None = None) -> dict[str, Any]:
        announcement = NodeAnnouncement(
            node=self.id,
            version=self.version,
            capacity=self.capacity,
            health=health,
        )
        return self.storage.announce(self._announcement_key, announcement.model_dump(mode="json"))

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
            return self.storage.set_leader(self.id, term)
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
        return self.storage.enqueue(task)

    def adopt_task(self) -> dict[str, Any] | None:
        """Lease one global task for this node to execute."""
        return self.storage.lease_task(self.id)
