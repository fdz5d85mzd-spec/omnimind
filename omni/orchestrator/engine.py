"""Meta-Orchestrator: assignment, balancing, bottleneck/duplicate detection,
auto spawn/terminate, workload prediction, model selection.

Responsibilities from the OmniMind spec
---------------------------------------
* monitor all running agents (heartbeat)         -> heartbeat()
* measure performance                            -> per-agent cost + eval aggregation
* detect bottlenecks                             -> detect_bottlenecks()
* detect duplicated work                         -> detect_duplicates()
* reassign tasks automatically                   -> balance()
* spawn new agents when needed                   -> spawn_if_needed()
* terminate idle agents                          -> terminate_idle()
* balance workloads                              -> balance()
* predict future workloads                       -> predict_next_tasks()
* optimize model selection                       -> choose_model()
* optimize execution costs / time                -> scoring strategies
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

from omni.contracts.agent import AgentSpec, AgentStatus, AgentType, TaskSpec, TaskStatus

# ---------------------------------------------------------------- scoring strategies
Strategy = Callable[[AgentSpec, TaskSpec], float]


def BalancedScoring(agent: AgentSpec, task: TaskSpec) -> float:
    """Prefer the least-loaded capable agent; priority lifts queued tasks."""
    load = agent.load + (min(agent.queue_depth, 5) / 5) * 0.25
    if task.required_skills and not set(task.required_skills) <= set(agent.skills):
        return -1.0  # incapable
    return (1.0 - load) + min(task.priority, 10) * 0.01


def CostOptimizedScoring(agent: AgentSpec, task: TaskSpec) -> float:
    """Prefer agents with the lowest cumulative spend; incapable agents fail."""
    if task.required_skills and not set(task.required_skills) <= set(agent.skills):
        return -1.0
    return 1.0 - min(agent.cost_spent, 1000.0) / 1000.0


def SpeedOptimizedScoring(agent: AgentSpec, task: TaskSpec) -> float:
    """Prefer agents with the shortest queue; incapable agents fail."""
    if task.required_skills and not set(task.required_skills) <= set(agent.skills):
        return -1.0
    return 1.0 - min(agent.queue_depth, 10) / 10.0


# ---------------------------------------------------------------- views
@dataclass
class BottleneckView:
    agent_id: str
    load: float
    queue_depth: int
    threshold: str


@dataclass
class Reassignment:
    task_id: str
    from_agent: str
    to_agent: str
    reason: str


@dataclass
class ModelOption:
    provider: str
    model: str
    cost_per_1k: float
    latency_ms: float
    quality: float  # 0..1

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


OptimizationMode = Literal["cost", "speed", "quality", "balanced"]


class MetaOrchestrator:
    def __init__(
        self,
        strategy: Strategy = BalancedScoring,
        max_agents: int = 50,
        spawn_threshold: float = 0.75,
        idle_ttl_s: float = 300.0,
        keep_min_idle: int = 2,
        model_catalog: list[ModelOption] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._agents: dict[str, AgentSpec] = {}
        self._tasks: dict[str, TaskSpec] = {}
        self.strategy = strategy
        self.max_agents = max_agents
        self.spawn_threshold = spawn_threshold
        self.idle_ttl_s = idle_ttl_s
        self.keep_min_idle = keep_min_idle
        self.model_catalog = model_catalog or default_model_catalog()
        # EWMA arrival rate (tasks per second)
        self._arrival_rate = 0.0
        self._last_tick = time.time()

    # ------------------------------------------------------------ registry
    def register_agent(self, name: str, skills: list[str] | None = None, agent_type: AgentType = AgentType.WORKER) -> AgentSpec:
        with self._lock:
            agent = AgentSpec(name=name, skills=skills or [], agent_type=agent_type)
            self._agents[agent.id] = agent
            return agent

    def heartbeat(self, agent_id: str, load: float | None = None, status: AgentStatus | None = None, cost_spent: float | None = None) -> AgentSpec:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(f"unknown agent '{agent_id}'")
            if load is not None:
                agent.load = max(0.0, min(1.0, load))
            if status is not None:
                agent.status = status
            if cost_spent is not None:
                agent.cost_spent = cost_spent
            agent.last_heartbeat = datetime.now(timezone.utc)
            return agent

    def agents(self) -> list[AgentSpec]:
        with self._lock:
            return list(self._agents.values())

    def tasks(self) -> list[TaskSpec]:
        with self._lock:
            return list(self._tasks.values())

    # ------------------------------------------------------------ intake
    def submit(self, task: TaskSpec) -> TaskSpec:
        with self._lock:
            self._tasks[task.id] = task
            now = time.time()
            dt = max(now - self._last_tick, 1e-6)
            self._arrival_rate = 0.7 * (1.0 / dt) + 0.3 * self._arrival_rate
            self._last_tick = now
            return task

    def assign(self, task_id: str) -> str | None:
        """Pick the best agent for a queued task. Returns assigned agent id or None."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"unknown task '{task_id}'")
            if task.status != TaskStatus.QUEUED:
                return task.assignee
            best: tuple[float, AgentSpec] | None = None
            for agent in self._agents.values():
                if agent.status == AgentStatus.TERMINATED:
                    continue
                score = self.strategy(agent, task)
                if score < 0:
                    continue
                if best is None or score > best[0]:
                    best = (score, agent)
            if best is None:
                return None
            _, agent = best
            task.status = TaskStatus.ASSIGNED
            task.assignee = agent.id
            task.started_at = datetime.now(timezone.utc)
            agent.queue_depth += 1
            agent.status = AgentStatus.RUNNING
            return agent.id

    def complete(self, task_id: str, result: object = None, cost: float = 0.0) -> TaskSpec:
        with self._lock:
            task = self._tasks[task_id]
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.finished_at = datetime.now(timezone.utc)
            if task.assignee:
                agent = self._agents.get(task.assignee)
                if agent:
                    agent.queue_depth = max(0, agent.queue_depth - 1)
                    agent.cost_spent += cost
                    agent.load = max(0.0, agent.load - 0.1)
            return task

    # ------------------------------------------------------------ monitoring
    def detect_bottlenecks(self, load_threshold: float = 0.8, queue_threshold: int = 10) -> list[BottleneckView]:
        with self._lock:
            out: list[BottleneckView] = []
            for agent in self._agents.values():
                if agent.status == AgentStatus.TERMINATED:
                    continue
                if agent.load >= load_threshold or agent.queue_depth >= queue_threshold:
                    out.append(
                        BottleneckView(
                            agent_id=agent.id,
                            load=agent.load,
                            queue_depth=agent.queue_depth,
                            threshold="load" if agent.load >= load_threshold else "queue",
                        )
                    )
            return out

    def detect_duplicates(self) -> list[list[str]]:
        """Group queued/assigned/running tasks sharing an identical signature."""
        with self._lock:
            groups: dict[str, list[str]] = {}
            for task in self._tasks.values():
                if task.status not in (TaskStatus.QUEUED, TaskStatus.ASSIGNED, TaskStatus.RUNNING):
                    continue
                if not task.signature:
                    continue
                groups.setdefault(task.signature, []).append(task.id)
            return [g for g in groups.values() if len(g) > 1]

    def spawn_if_needed(self, name_prefix: str = "worker") -> list[str]:
        """If max load across agents crosses the spawn threshold, spawn one agent."""
        with self._lock:
            active = [a for a in self._agents.values() if a.status != AgentStatus.TERMINATED]
            if len(active) >= self.max_agents:
                return []
            if not active:
                agent = self.register_agent(f"{name_prefix}-1")
                return [agent.id]
            avg_load = sum(a.load for a in active) / len(active)
            if avg_load < self.spawn_threshold:
                return []
            agent = self.register_agent(f"{name_prefix}-{len(active) + 1}")
            return [agent.id]

    def terminate_idle(self) -> list[str]:
        """Terminate agents idle beyond TTL, keeping a minimum warm pool."""
        with self._lock:
            now = time.time()
            idle = [
                a
                for a in self._agents.values()
                if a.status == AgentStatus.IDLE
                and (now - a.last_heartbeat.timestamp()) > self.idle_ttl_s
            ]
            idle.sort(key=lambda a: a.last_heartbeat.timestamp())
            keep = self.keep_min_idle
            terminated: list[str] = []
            for agent in idle:
                if keep > 0:
                    keep -= 1
                    continue
                agent.status = AgentStatus.TERMINATED
                terminated.append(agent.id)
            return terminated

    def balance(self, high_threshold: float = 0.8, low_threshold: float = 0.2) -> list[Reassignment]:
        """Move queued tasks from overloaded agents to underloaded ones."""
        with self._lock:
            overloaded = [
                a for a in self._agents.values()
                if a.status != AgentStatus.TERMINATED and a.load >= high_threshold and a.queue_depth > 0
            ]
            underloaded = [
                a for a in self._agents.values()
                if a.status != AgentStatus.TERMINATED and a.load <= low_threshold
            ]
            if not overloaded or not underloaded:
                return []
            moves: list[Reassignment] = []
            for src in overloaded:
                if not underloaded:
                    break
                for task in list(self._tasks.values()):
                    if len(moves) >= 3:
                        break
                    if task.status == TaskStatus.ASSIGNED and task.assignee == src.id:
                        dst = min(underloaded, key=lambda a: a.load)
                        if self.strategy(dst, task) < 0:
                            continue
                        task.assignee = dst.id
                        task.status = TaskStatus.ASSIGNED
                        src.queue_depth = max(0, src.queue_depth - 1)
                        src.load = max(0.0, src.load - 0.1)
                        dst.queue_depth += 1
                        dst.load = min(1.0, dst.load + 0.1)
                        moves.append(
                            Reassignment(task_id=task.id, from_agent=src.id, to_agent=dst.id, reason="load balancing")
                        )
            return moves

    # ------------------------------------------------------------ prediction
    def arrival_rate(self) -> float:
        """Current EWMA arrival rate in tasks/second."""
        with self._lock:
            return self._arrival_rate

    def total_cost(self) -> float:
        """Cumulative spend across all agents."""
        with self._lock:
            return round(sum(a.cost_spent for a in self._agents.values()), 4)

    def predict_next_tasks(self, horizon_minutes: float = 10.0) -> float:
        """EWMA arrival rate x horizon."""
        return round(self._arrival_rate * horizon_minutes * 60.0, 1)

    # ------------------------------------------------------------ model selection
    def choose_model(self, task: TaskSpec, mode: OptimizationMode = "balanced") -> ModelOption:
        """Pick the best model from the catalog for this task and optimization mode."""
        catalog = self.model_catalog
        by_mode = {
            "cost": lambda m: (m.cost_per_1k,),
            "speed": lambda m: (m.latency_ms,),
            "quality": lambda m: (-m.quality,),
            "balanced": lambda m: (m.cost_per_1k + m.latency_ms / 100.0 - m.quality,),
        }
        ranked = sorted(catalog, key=by_mode[mode])
        return ranked[0]

    def report(self) -> dict:
        with self._lock:
            agents = [a for a in self._agents.values() if a.status != AgentStatus.TERMINATED]
            cpu = sum(a.load for a in agents) / len(agents) if agents else 0.0
            busy = [a for a in agents if a.status == AgentStatus.RUNNING]
            return {
                "agents_total": len(agents),
                "agents_running": len(busy),
                "agents_idle": len([a for a in agents if a.status == AgentStatus.IDLE]),
                "tasks_total": len(self._tasks),
                "tasks_queued": len([t for t in self._tasks.values() if t.status == TaskStatus.QUEUED]),
                "tasks_completed": len([t for t in self._tasks.values() if t.status == TaskStatus.COMPLETED]),
                "avg_load": round(cpu, 3),
                "predicted_tasks_next_10m": self.predict_next_tasks(10),
                "bottlenecks": len(self.detect_bottlenecks()),
                "duplicate_groups": len(self.detect_duplicates()),
            }


def default_model_catalog() -> list[ModelOption]:
    return [
        ModelOption(provider="openai", model="gpt-4o-mini", cost_per_1k=0.15, latency_ms=400, quality=0.85),
        ModelOption(provider="anthropic", model="claude-3-5-haiku", cost_per_1k=0.25, latency_ms=500, quality=0.88),
        ModelOption(provider="google", model="gemini-2-flash", cost_per_1k=0.10, latency_ms=350, quality=0.82),
        ModelOption(provider="openai", model="gpt-4o", cost_per_1k=2.50, latency_ms=900, quality=0.97),
        ModelOption(provider="anthropic", model="claude-3-5-sonnet", cost_per_1k=3.00, latency_ms=1100, quality=0.98),
    ]
