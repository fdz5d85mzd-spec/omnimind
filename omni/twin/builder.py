"""Digital Twin: build a live snapshot graph of the whole operating system:
agents (running / sleeping / waiting), task dependencies, skill installations,
memory keys, costs, model usage, CPUs, queues, and errors.

Every decision is replayable because the twin is derived from the live state
of the orchestrator, marketplace, memory, simulation, and learning pipeline —
not from a separate persisted model.

NOT IMPLEMENTED (roadmap M6b): persistent replay ledger, historical trend
series for counters (CPU/RAM/network), and the live WebSocket stream. The
snapshot builder itself is complete and tested.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from omni.contracts.agent import AgentStatus, TaskStatus

_COUNTERS = ("agents_running", "agents_waiting", "agents_idle", "tasks_queued", "tasks_running")


class TwinBuilder:
    def __init__(self, orchestrator=None, marketplace=None, memory=None, simulation=None, learning=None, ledger=None) -> None:
        self._orchestrator = orchestrator
        self._marketplace = marketplace
        self._memory = memory
        self._simulation = simulation
        self._learning = learning
        self._ledger = ledger
        self._lock = threading.RLock()
        self._tick = 0
        self._errors: list[dict[str, Any]] = []

    def replay(self, subject: str | None = None, subsystem: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Replay recorded system decisions (from the ReplayLedger). One of the
two live observability primitives: snapshot() for state, replay() for history."""
        if self._ledger is None:
            return []
        return self._ledger.replay(subject=subject, subsystem=subsystem, limit=limit)

    def record_error(self, source: str, message: str) -> None:
        with self._lock:
            self._errors.append({"at": time.time(), "source": source, "message": message})
            self._errors = self._errors[-200:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._tick += 1
            agents: list[dict[str, Any]] = []
            agent_edges: list[dict[str, str]] = []
            tasks: list[dict[str, Any]] = []
            if self._orchestrator is not None:
                for agent in self._orchestrator.agents():
                    agents.append(
                        {
                            "id": agent.id,
                            "name": agent.name,
                            "type": agent.agent_type.value,
                            "status": agent.status.value,
                            "load": agent.load,
                            "queue_depth": agent.queue_depth,
                            "model": agent.model,
                            "cost_spent": agent.cost_spent,
                        }
                    )
                for task in self._orchestrator.tasks():
                    tasks.append(
                        {
                            "id": task.id,
                            "name": task.name,
                            "status": task.status.value,
                            "priority": task.priority,
                            "risk_level": task.risk_level,
                            "assignee": task.assignee,
                        }
                    )
                    if task.assignee:
                        agent_edges.append({"from": task.assignee, "to": task.id, "relation": "executes"})

            skills = []
            if self._marketplace is not None:
                for skill in self._marketplace.all():
                    skills.append(
                        {
                            "id": skill.id,
                            "name": skill.name,
                            "kind": skill.kind.value,
                            "version": skill.latest_version,
                            "installs": skill.installs,
                            "rating": skill.rating,
                        }
                    )

            memory = []
            if self._memory is not None:
                for key in self._memory.all_keys():
                    memory.append({"key": key, "branches": list((self._memory.snapshot().get(key) or {}).keys())})

            simulations = []
            if self._simulation is not None:
                for sim in self._simulation.history():
                    simulations.append(
                        {
                            "id": sim.simulation_id,
                            "domain": sim.domain,
                            "confidence": sim.confidence,
                            "risk_level": sim.risk_level.value,
                            "needs_approval": sim.needs_approval,
                        }
                    )

            learning = {}
            if self._learning is not None:
                learning = {
                    "samples": len(self._learning.evaluations()),
                    "trends": self._learning.trends(),
                }

            status_counts: dict[str, int] = {"running": 0, "sleeping": 0, "waiting": 0}
            for a in agents:
                if a["status"] == AgentStatus.RUNNING.value:
                    status_counts["running"] += 1
                elif a["status"] == AgentStatus.WAITING.value:
                    status_counts["waiting"] += 1
                else:
                    status_counts["sleeping"] += 1

            return {
                "tick": self._tick,
                "generated_at": time.time(),
                "agents": agents,
                "agent_status_counts": status_counts,
                "tasks": tasks,
                "task_dependencies": agent_edges,
                "skills": skills,
                "memory_keys": memory,
                "simulations": simulations,
                "learning": learning,
                "errors": list(self._errors),
                "counters": {
                    "agents_total": len(agents),
                    "tasks_queued": sum(1 for t in tasks if t["status"] == TaskStatus.QUEUED.value),
                    "tasks_running": sum(1 for t in tasks if t["status"] == TaskStatus.RUNNING.value),
                    "costs_total": round(sum(a["cost_spent"] for a in agents), 4),
                    "queues_total": sum(a["queue_depth"] for a in agents),
                    "errors_last_window": len(self._errors),
                },
            }
