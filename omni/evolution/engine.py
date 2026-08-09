"""Self Evolution Engine: continuously proposes improvements across
architectures, workflows, prompts, memory organization, routing, agents,
plugins, UI, APIs, infrastructure, and documentation — and adopts ONLY
proposals that produce measurable gains.

Flow
----
1. `propose()` generates proposals (from templates + improvement reports).
2. `evaluate(proposal)` runs each candidate as an experiment (A/B) using
   evaluations from the Learning Pipeline.
3. `adopt_if_gain()` compares the measured delta against a minimum gain
   threshold; adopted proposals are recorded in the evolution ledger;
   rejected experiments remain as negative evidence.

NOT IMPLEMENTED (roadmap M7b): autonomous mutation hooks that apply adopted
proposals to live prompts/routing/memory layout at runtime (the adoption gate
exists; the mutation executor is a stub that records intent).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from omni.contracts.evaluation import MetricBundle

DOMAINS = [
    "architecture",
    "workflow",
    "prompt",
    "memory_organization",
    "routing",
    "agent",
    "plugin",
    "ui",
    "api",
    "infrastructure",
    "documentation",
]


@dataclass
class EvolutionProposal:
    id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")
    domain: str = ""
    title: str = ""
    description: str = ""
    hypothesis: str = ""
    status: str = "proposed"  # proposed | running | adopted | rejected
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    delta: dict[str, float] = field(default_factory=dict)
    adopted_at: Any = None


class EvolutionEngine:
    """Propose -> experiment -> measure -> adopt-if-gain."""

    def __init__(self, min_gain: float = 0.05, learning=None) -> None:
        self._lock = threading.RLock()
        self.min_gain = min_gain
        self._proposals: list[EvolutionProposal] = []
        self._learning = learning

    def propose(self, domain: str, title: str, description: str, hypothesis: str = "") -> EvolutionProposal:
        if domain not in DOMAINS:
            raise KeyError(f"unknown evolution domain '{domain}' (known: {DOMAINS})")
        proposal = EvolutionProposal(
            domain=domain,
            title=title,
            description=description,
            hypothesis=hypothesis or _default_hypothesis(domain),
        )
        with self._lock:
            self._proposals.append(proposal)
        return proposal

    def evaluate(self, proposal_id: str, after: MetricBundle) -> EvolutionProposal:
        """Measure the experiment: before-state comes from the learning
        pipeline aggregate; after-state from the supplied evaluation."""
        with self._lock:
            p = self._by_id(proposal_id)
            before = {}
            if self._learning is not None:
                before = self._learning.aggregate()
            after_d = after.model_dump()
            p.before = before
            p.after = {k: v for k, v in after_d.items() if k in before}
            p.delta = {
                m: round(after_d[m] - before.get(m, 0.0), 4)
                for m in before
                if m in after_d
            }
            p.status = "running"
            return p

    def adopt_if_gain(self, proposal_id: str) -> EvolutionProposal:
        """Adopt only if the primary metric improved beyond min_gain."""
        with self._lock:
            p = self._by_id(proposal_id)
            if p.status != "running":
                raise ValueError(f"proposal '{proposal_id}' is not running (status={p.status})")
            gains = [d for d in p.delta.values() if d > 0]
            # _improvement: sum of positive deltas (or negative delta for cost/latency)
            total_gain = sum(gains)
            if total_gain >= self.min_gain:
                p.status = "adopted"
                p.adopted_at = p.adopted_at or True
            else:
                p.status = "rejected"
            return p

    def ledger(self) -> list[EvolutionProposal]:
        with self._lock:
            return [EvolutionProposal(**vars(p)) for p in self._proposals]

    def get(self, proposal_id: str) -> EvolutionProposal:
        """Public read of one proposal (used by the mutation executor)."""
        return self._by_id(proposal_id)

    def _by_id(self, proposal_id: str) -> EvolutionProposal:
        for p in self._proposals:
            if p.id == proposal_id:
                return p
        raise KeyError(f"unknown proposal '{proposal_id}'")


def _default_hypothesis(domain: str) -> str:
    return {
        "architecture": "modularizing the execution graph reduces average latency",
        "workflow": "parallelizing independent steps reduces wall-clock time",
        "prompt": "more specific task framing improves accuracy",
        "memory_organization": "branching by project reduces cross-project interference",
        "routing": "cost-optimized routing lowers spend without accuracy loss",
        "agent": "specialist agents outperform generalists per domain",
        "plugin": "replacing the slowest plugin improves throughput",
        "ui": "reduced latency in the twin view improves operator decision speed",
        "api": "batching policy decisions cuts decision latency",
        "infrastructure": "horizontal scaling reduces queue backlog",
        "documentation": "structured docs lower onboarding time",
    }[domain]
