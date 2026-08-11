"""AgentRunner — the entry point behind the public "ask anything" agent.

Wires together the platform's own subsystems for one end-to-end request:
the Policy Engine gates it, the Meta-Orchestrator tracks it as a real
agent + task, Versioned Memory records the exchange, the fleet bus
publishes each stage as a live event (visible to any client connected to
/twin/stream), and the LLM abstraction produces the actual answer. Every
published stage marks a real thing that just happened in the backend —
none of it is theater for a progress bar.

`run()` returns the complete answer in one shot; `run_stream()` yields it
as it's generated (text deltas) — what the chat UI actually calls, so
typing starts immediately instead of waiting for the full completion.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from omni.agents.llm import LLMError, LLMNotConfigured, call_llm, stream_llm
from omni.agents.router import classify_skills
from omni.contracts.agent import AgentType, TaskSpec
from omni.contracts.policy import Principal, Resource
from omni.fleet.bus import FleetBus
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine

_BASE_SYSTEM_PROMPT = (
    "You are the OmniMind agent, part of an autonomous, self-evolving AI "
    "operating system. Answer the user's request directly and usefully: "
    "research, explain, draft, or plan, as asked. Be concrete and concise."
)

# One line of framing per specialist skill, appended when the orchestrator
# actually routed the request to that kind of fleet agent (see
# fleet_seed.SEED_ROSTER for the roster this has to line up with) -- so a
# request routed to the Code Agent genuinely reads differently than one
# routed to the Research Agent, instead of every specialist sharing one
# generic prompt regardless of who the orchestrator says is handling it.
_SKILL_FRAMING: dict[str, str] = {
    "coding": "You're acting as the fleet's Code Agent: prioritize correct, runnable code, name concrete pitfalls, and keep prose around code minimal.",
    "code-review": "You're acting as the fleet's Code Agent doing a code review: call out bugs, risks, and concrete fixes, not general style opinions.",
    "research": "You're acting as the fleet's Research Agent: prioritize verifiable facts, flag uncertainty explicitly, and cite what kind of source would confirm each claim.",
    "writing": "You're acting as the fleet's Writer Agent: prioritize clear, well-structured prose in the tone the request implies.",
    "translation": "You're acting as the fleet's Translator: prioritize accurate, natural translation over literal word-for-word conversion.",
    "planning": "You're acting as the fleet's Planner Agent: structure the answer as concrete, ordered steps with dependencies made explicit.",
    "data-analysis": "You're acting as the fleet's Data Analyst: prioritize precise numbers and caveat any inference that isn't directly supported by what's given.",
    "summarization": "You're acting as the fleet's Summarizer: prioritize brevity and cutting anything not essential to the source.",
}


# Tiered model routing: routine WORKER agents (logging, notifications,
# triage, backups...) run high volume, low-stakes requests that don't need
# a frontier model, so they're routed to the cheap/fast tier. Specialists
# and the fleet leader (SUPERVISOR) do the actual product-facing work and
# stay on the production tier. Both are env-overridable per deployment
# without touching code, same convention as the old ANTHROPIC_MODEL var.
_MODEL_HAIKU = os.environ.get("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5-20251001")
_MODEL_SONNET = os.environ.get("ANTHROPIC_MODEL_SONNET", "claude-sonnet-5")


def _model_for(agent_type: AgentType) -> str:
    if agent_type == AgentType.WORKER:
        return _MODEL_HAIKU
    return _MODEL_SONNET


def _system_prompt(agent_skills: list[str] | None = None) -> str:
    # The model has no built-in clock, so "what's today's date" or "who's
    # celebrating today" fails unless the real current date is handed to it
    # on every request. Athens time since the product's audience is Greek.
    now = datetime.now(ZoneInfo("Europe/Athens"))
    parts = [
        _BASE_SYSTEM_PROMPT,
        f"Current date and time: {now.strftime('%A, %d %B %Y, %H:%M')} (Europe/Athens).",
    ]
    for skill in agent_skills or []:
        framing = _SKILL_FRAMING.get(skill)
        if framing:
            parts.append(framing)
            break  # one specialist framing is enough; skills are priority-ordered
    return "\n\n".join(parts)


@dataclass
class AgentRunResult:
    run_id: str
    status: str  # "completed" | "failed" | "denied"
    prompt: str
    answer: str | None = None
    error: str | None = None
    task_id: str | None = None
    agent_name: str | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "prompt": self.prompt,
            "answer": self.answer,
            "error": self.error,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "duration_ms": self.duration_ms,
        }


@dataclass
class _Setup:
    run_id: str
    agent_id: str
    agent_name: str
    agent_skills: list[str]
    model: str
    task_id: str
    started: float


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

    def _publish(self, run_id: str, session_id: str, stage: str, payload: dict[str, Any]) -> None:
        if self._bus is not None:
            self._bus.publish(f"agent.{run_id}.{stage}", {"session_id": session_id, **payload})

    def _setup(self, prompt: str, session_id: str) -> _Setup | AgentRunResult:
        """Policy check, memory write, orchestrator registration — identical
        prelude for both run() and run_stream(). Returns an AgentRunResult
        directly if the policy denies the request (nothing left to do)."""
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        started = time.monotonic()
        principal = Principal(id=f"web-{session_id}", roles=["web-user"])
        resource = Resource(type="agent_run", attributes={"risk_level": "low"})

        self._publish(run_id, session_id, "started", {"prompt": prompt})

        decision = self._policy.evaluate(principal, "agent.run", resource)
        self._publish(run_id, session_id, "policy_evaluated", {"allowed": decision.allowed, "reason": decision.reason})
        if not decision.allowed:
            self._publish(run_id, session_id, "denied", {"reason": decision.reason})
            return AgentRunResult(run_id=run_id, status="denied", prompt=prompt, error=decision.reason)

        entry = self._memory.write(
            key=f"agent_run.{run_id}.prompt",
            value={"prompt": prompt},
            agent_id=principal.id,
            reason="user request",
        )
        self._publish(run_id, session_id, "memory_stored", {"key": entry.key, "version": entry.version})

        # Route to a real specialist from the seeded fleet (fleet_seed.py)
        # by what the prompt actually needs, instead of spinning up a fresh
        # disposable worker per request that always wins assignment purely
        # by having zero load. required_skills takes the single best match
        # -- fleet specialists each carry one primary skill, so asking for
        # more than one at once would just make every agent ineligible.
        required_skills = classify_skills(prompt)[:1]
        task = self._orchestrator.submit(
            TaskSpec(
                name=f"agent.run {run_id}",
                risk_level="low",
                payload={"prompt": prompt},
                required_skills=required_skills,
            )
        )
        agent_id = self._orchestrator.assign(task.id)
        agent = self._orchestrator.get_agent(agent_id) if agent_id else None
        if agent is None:
            # No seeded agent was free/capable (e.g. fresh test orchestrator
            # with no fleet registered yet) -- fall back to a disposable
            # worker so the request is never simply dropped. Carries
            # required_skills itself so the still-QUEUED task can actually
            # match it on this second assign() pass.
            agent = self._orchestrator.register_agent(
                f"web-agent-{run_id[-6:]}", skills=required_skills or ["chat"], agent_type=AgentType.WORKER
            )
            self._orchestrator.assign(task.id)
        self._publish(
            run_id, session_id, "task_assigned",
            {"task_id": task.id, "agent_id": agent.id, "agent_name": agent.name},
        )

        return _Setup(
            run_id=run_id,
            agent_id=agent.id,
            agent_name=agent.name,
            agent_skills=agent.skills,
            model=_model_for(agent.agent_type),
            task_id=task.id,
            started=started,
        )

    def run(
        self,
        prompt: str,
        session_id: str = "anonymous",
        user_api_key: str | None = None,
        user_api_provider: str | None = None,
    ) -> AgentRunResult:
        setup = self._setup(prompt, session_id)
        if isinstance(setup, AgentRunResult):
            return setup

        self._publish(setup.run_id, session_id, "thinking", {"agent_name": setup.agent_name})
        try:
            answer = call_llm(
                prompt,
                system=_system_prompt(setup.agent_skills),
                model=setup.model,
                user_api_key=user_api_key,
                user_provider=user_api_provider,
            )
        except (LLMNotConfigured, LLMError) as e:
            self._orchestrator.fail(setup.task_id, error=str(e))
            self._publish(setup.run_id, session_id, "failed", {"error": str(e)})
            return AgentRunResult(
                run_id=setup.run_id, status="failed", prompt=prompt, task_id=setup.task_id, error=str(e)
            )

        self._memory.write(
            key=f"agent_run.{setup.run_id}.answer",
            value={"answer": answer},
            agent_id=setup.agent_id,
            reason="agent response",
        )
        self._orchestrator.complete(setup.task_id, result={"answer": answer})
        duration_ms = (time.monotonic() - setup.started) * 1000
        self._publish(
            setup.run_id, session_id, "completed",
            {"answer": answer, "duration_ms": duration_ms, "agent_name": setup.agent_name},
        )

        return AgentRunResult(
            run_id=setup.run_id,
            status="completed",
            prompt=prompt,
            answer=answer,
            task_id=setup.task_id,
            agent_name=setup.agent_name,
            duration_ms=duration_ms,
        )

    def run_stream(
        self,
        prompt: str,
        session_id: str = "anonymous",
        user_api_key: str | None = None,
        user_api_provider: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Same pipeline as run(), but yields the answer as text deltas
        arrive from the LLM provider. Each yielded dict is one of:
          {"type": "delta", "text": str}
          {"type": "done", "run_id", "answer", "duration_ms"}
          {"type": "denied"|"failed", "run_id", "error"}
        """
        setup = self._setup(prompt, session_id)
        if isinstance(setup, AgentRunResult):
            yield {"type": setup.status, "run_id": setup.run_id, "error": setup.error}
            return

        self._publish(setup.run_id, session_id, "thinking", {"agent_name": setup.agent_name})
        chunks: list[str] = []
        try:
            for delta in stream_llm(
                prompt,
                system=_system_prompt(setup.agent_skills),
                model=setup.model,
                user_api_key=user_api_key,
                user_provider=user_api_provider,
            ):
                chunks.append(delta)
                yield {"type": "delta", "text": delta}
        except (LLMNotConfigured, LLMError) as e:
            self._orchestrator.fail(setup.task_id, error=str(e))
            self._publish(setup.run_id, session_id, "failed", {"error": str(e)})
            yield {"type": "failed", "run_id": setup.run_id, "error": str(e)}
            return

        answer = "".join(chunks)
        self._memory.write(
            key=f"agent_run.{setup.run_id}.answer",
            value={"answer": answer},
            agent_id=setup.agent_id,
            reason="agent response",
        )
        self._orchestrator.complete(setup.task_id, result={"answer": answer})
        duration_ms = (time.monotonic() - setup.started) * 1000
        self._publish(
            setup.run_id, session_id, "completed",
            {"answer": answer, "duration_ms": duration_ms, "agent_name": setup.agent_name},
        )
        yield {
            "type": "done",
            "run_id": setup.run_id,
            "answer": answer,
            "duration_ms": duration_ms,
            "agent_name": setup.agent_name,
        }
