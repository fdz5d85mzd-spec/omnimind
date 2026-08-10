"""AgentRunner — the entry point behind the public "ask anything" agent.

Wires together the platform's own subsystems for one end-to-end request:
the Policy Engine gates it, the Meta-Orchestrator tracks it as a real
agent + task, Versioned Memory records the exchange, the fleet bus
publishes each stage as a live event (visible to any client connected to
/twin/stream), and the LLM abstraction produces the actual answer. Every
published stage marks a real thing that just happened in the backend —
none of it is theater for a progress bar.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from omni.agents.llm import LLMError, LLMNotConfigured, call_llm
from omni.contracts.agent import AgentType, TaskSpec
from omni.contracts.policy import Principal, Resource
from omni.fleet.bus import FleetBus
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine

SYSTEM_PROMPT = (
    "You are the OmniMind agent, part of an autonomous, self-evolving AI "
    "operating system. Answer the user's request directly and usefully: "
    "research, explain, draft, or plan, as asked. Be concrete and concise."
)


@dataclass
class AgentRunResult:
    run_id: str
    status: str  # "completed" | "failed" | "denied"
    prompt: str
    answer: str | None = None
    error: str | None = None
    task_id: str | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "prompt": self.prompt,
            "answer": self.answer,
            "error": self.error,
            "task_id": self.task_id,
            "duration_ms": self.duration_ms,
        }


class AgentRunner:
    def __init__(
        self,
        policy: PolicyEngine,
        memory: MemoryStore,
        orchestrator: MetaOrchestrator,
        bus: FleetBus | None = None,
    ) -> None:
        self._policy = policy
        self._memory = memory
        self._orchestrator = orchestrator
        self._bus = bus

    def _publish(self, run_id: str, stage: str, payload: dict[str, Any]) -> None:
        if self._bus is not None:
            self._bus.publish(f"agent.{run_id}.{stage}", payload)

    def run(self, prompt: str, session_id: str = "anonymous") -> AgentRunResult:
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        started = time.monotonic()
        principal = Principal(id=f"web-{session_id}", roles=["web-user"])
        resource = Resource(type="agent_run", attributes={"risk_level": "low"})

        self._publish(run_id, "started", {"prompt": prompt})

        decision = self._policy.evaluate(principal, "agent.run", resource)
        self._publish(run_id, "policy_evaluated", {"allowed": decision.allowed, "reason": decision.reason})
        if not decision.allowed:
            self._publish(run_id, "denied", {"reason": decision.reason})
            return AgentRunResult(run_id=run_id, status="denied", prompt=prompt, error=decision.reason)

        entry = self._memory.write(
            key=f"agent_run.{run_id}.prompt",
            value={"prompt": prompt},
            agent_id=principal.id,
            reason="user request",
        )
        self._publish(run_id, "memory_stored", {"key": entry.key, "version": entry.version})

        agent = self._orchestrator.register_agent(
            f"web-agent-{run_id[-6:]}", skills=["chat"], agent_type=AgentType.WORKER
        )
        task = self._orchestrator.submit(
            TaskSpec(name=f"agent.run {run_id}", risk_level="low", payload={"prompt": prompt})
        )
        self._orchestrator.assign(task.id)
        self._publish(run_id, "task_assigned", {"task_id": task.id, "agent_id": agent.id})

        self._publish(run_id, "thinking", {})
        try:
            answer = call_llm(prompt, system=SYSTEM_PROMPT)
        except (LLMNotConfigured, LLMError) as e:
            self._orchestrator.complete(task.id, result={"error": str(e)})
            self._publish(run_id, "failed", {"error": str(e)})
            return AgentRunResult(run_id=run_id, status="failed", prompt=prompt, task_id=task.id, error=str(e))

        self._memory.write(
            key=f"agent_run.{run_id}.answer", value={"answer": answer}, agent_id=agent.id, reason="agent response"
        )
        self._orchestrator.complete(task.id, result={"answer": answer})
        duration_ms = (time.monotonic() - started) * 1000
        self._publish(run_id, "completed", {"answer": answer, "duration_ms": duration_ms})

        return AgentRunResult(
            run_id=run_id, status="completed", prompt=prompt, answer=answer, task_id=task.id, duration_ms=duration_ms
        )
