"""Periodic self-review: looks at real fleet/task/policy signals and, when
something concrete stands out, proposes an improvement through the
existing Self Evolution Engine (omni/evolution/engine.py).

This module only ever *proposes*. Turning a proposal into something that
actually changes live behavior needs two further, separate, human-gated
steps that already exist and are untouched by this module:

  1. `EvolutionEngine.evaluate()` + `.adopt_if_gain()` — requires a real
     A/B measurement, not automated here.
  2. `EvolutionExecutor.apply()` — requires the proposal to be adopted
     AND passes through the Policy Engine's `evolution.apply` rule, which
     only the `system.admin` role may satisfy (see make_seed_rules()).

The reflection cycle itself runs as role `operator`, which the policy
seed rules do not grant `evolution.apply` -- so even a compromised or
buggy reflection loop cannot apply its own proposals; that requires a
human/admin action through the existing /evolution/apply endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from omni.agents.llm import LLMError, LLMNotConfigured, call_llm
from omni.contracts.policy import Principal, Resource
from omni.evolution.engine import DOMAINS, EvolutionEngine
from omni.fleet.bus import FleetBus
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine

logger = logging.getLogger(__name__)

_PRINCIPAL = Principal(id="learning-agent", roles=["operator"])

_SYSTEM_PROMPT = (
    "You're the fleet's Learning Agent / Cost Optimizer running a scheduled self-review. "
    "You're given a real snapshot of fleet metrics: task outcomes, per-agent load, and "
    "recent policy decisions. Identify at most ONE concrete, worthwhile improvement -- "
    "something an operator could actually act on, not generic advice. "
    "Reply with ONLY a JSON object, no prose before or after it, no markdown fences:\n"
    '{"has_proposal": true|false, "domain": one of ' + json.dumps(DOMAINS) + ", "
    '"title": "short string", "description": "string", "hypothesis": "string"}\n'
    "Set has_proposal to false and leave the other fields empty if the snapshot looks "
    "healthy and nothing concrete stands out -- never invent a proposal just to have one."
)


@dataclass
class ReflectionResult:
    run_id: str
    status: str  # "proposed" | "no_signal" | "denied" | "failed"
    snapshot: dict[str, Any] = field(default_factory=dict)
    proposal_id: str | None = None
    reasoning: str | None = None
    error: str | None = None


def _snapshot(orchestrator: MetaOrchestrator, policy: PolicyEngine) -> dict[str, Any]:
    tasks = orchestrator.tasks()
    agents = orchestrator.agents()
    status_counts: dict[str, int] = {}
    for t in tasks:
        status_counts[t.status.value] = status_counts.get(t.status.value, 0) + 1
    recent_decisions = policy.decision_log(limit=100)
    return {
        "task_count": len(tasks),
        "task_status_counts": status_counts,
        "agent_count": len(agents),
        "busiest_agents": sorted(
            ({"name": a.name, "skills": a.skills, "queue_depth": a.queue_depth} for a in agents),
            key=lambda a: a["queue_depth"],
            reverse=True,
        )[:5],
        "recent_policy_decisions_checked": len(recent_decisions),
        "recent_policy_denials": sum(1 for d in recent_decisions if not d.allowed),
    }


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def run_reflection_cycle(
    policy: PolicyEngine,
    memory: MemoryStore,
    orchestrator: MetaOrchestrator,
    evolution: EvolutionEngine,
    bus: FleetBus | None = None,
) -> ReflectionResult:
    run_id = f"reflect_{uuid.uuid4().hex[:10]}"

    decision = policy.evaluate(
        _PRINCIPAL, "learning.reflect", Resource(type="learning_reflect", attributes={"risk_level": "low"})
    )
    if not decision.allowed:
        return ReflectionResult(run_id=run_id, status="denied", error=decision.reason)

    snapshot = _snapshot(orchestrator, policy)
    if bus is not None:
        bus.publish(f"learning.{run_id}.started", {"snapshot": snapshot})

    prompt = "Fleet metrics snapshot (JSON):\n" + json.dumps(snapshot, indent=2)
    try:
        raw = call_llm(prompt, system=_SYSTEM_PROMPT, max_tokens=500)
    except (LLMNotConfigured, LLMError) as e:
        if bus is not None:
            bus.publish(f"learning.{run_id}.failed", {"error": str(e)})
        return ReflectionResult(run_id=run_id, status="failed", snapshot=snapshot, error=str(e))

    parsed = _extract_json(raw)
    memory.write(
        key=f"self_review.{run_id}.report",
        value={"snapshot": snapshot, "raw_response": raw, "parsed": parsed},
        agent_id=_PRINCIPAL.id,
        reason="scheduled self-review",
    )

    if parsed is None:
        if bus is not None:
            bus.publish(f"learning.{run_id}.failed", {"error": "unparseable response"})
        return ReflectionResult(
            run_id=run_id, status="failed", snapshot=snapshot, reasoning=raw,
            error="could not parse a structured proposal from the LLM response",
        )

    if not parsed.get("has_proposal"):
        if bus is not None:
            bus.publish(f"learning.{run_id}.completed", {"status": "no_signal"})
        return ReflectionResult(run_id=run_id, status="no_signal", snapshot=snapshot, reasoning=raw)

    try:
        proposal = evolution.propose(
            domain=parsed.get("domain", ""),
            title=parsed.get("title", "untitled"),
            description=parsed.get("description", ""),
            hypothesis=parsed.get("hypothesis", ""),
        )
    except KeyError as e:
        # model picked a domain outside DOMAINS -- the proposal is lost,
        # but the raw reasoning is already safely recorded in memory above
        if bus is not None:
            bus.publish(f"learning.{run_id}.failed", {"error": str(e)})
        return ReflectionResult(run_id=run_id, status="failed", snapshot=snapshot, reasoning=raw, error=str(e))

    if bus is not None:
        bus.publish(f"learning.{run_id}.completed", {"status": "proposed", "proposal_id": proposal.id})
    return ReflectionResult(
        run_id=run_id, status="proposed", snapshot=snapshot, proposal_id=proposal.id, reasoning=raw
    )


class ReflectionScheduler:
    """Same shape as fleet.scheduler.FleetScheduler: a real periodic tick,
    not a fake "online" indicator. Every interval, runs one reflection
    cycle and publishes it to the bus so it's visible live in Mission
    Control, same as the fleet rebalance tick."""

    def __init__(
        self,
        policy: PolicyEngine,
        memory: MemoryStore,
        orchestrator: MetaOrchestrator,
        evolution: EvolutionEngine,
        bus: FleetBus | None = None,
        interval_s: float = 21600.0,  # 6h -- cheap (one LLM call) but frequent enough to matter
    ) -> None:
        self._policy = policy
        self._memory = memory
        self._orchestrator = orchestrator
        self._evolution = evolution
        self._bus = bus
        self._interval_s = interval_s
        self._task = None

    def tick(self) -> ReflectionResult:
        return run_reflection_cycle(self._policy, self._memory, self._orchestrator, self._evolution, self._bus)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_s)
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive, never crash the loop
                logger.exception("reflection scheduler tick failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
