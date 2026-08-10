"""Reflection cycle tests: the Learning Agent's periodic self-review that
proposes improvements through the Evolution Engine, gated by the Policy
Engine, and never applies anything itself."""

import json
from unittest.mock import patch

from omni.agents.llm import LLMNotConfigured
from omni.agents.reflection import ReflectionScheduler, run_reflection_cycle
from omni.contracts.agent import AgentType, TaskSpec
from omni.evolution.engine import EvolutionEngine
from omni.fleet.bus import InMemoryBus
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine, make_seed_rules


def _setup(bus=None):
    policy = PolicyEngine(make_seed_rules())
    memory = MemoryStore()
    orchestrator = MetaOrchestrator()
    evolution = EvolutionEngine()
    return policy, memory, orchestrator, evolution


def _proposal_json(**overrides) -> str:
    body = {
        "has_proposal": True,
        "domain": "routing",
        "title": "Route summaries to the cheap tier",
        "description": "Summarizer tasks are running on the production model tier.",
        "hypothesis": "Cost drops with no quality loss.",
    }
    body.update(overrides)
    return json.dumps(body)


def test_proposal_is_recorded_in_the_evolution_engine():
    policy, memory, orchestrator, evolution = _setup()
    with patch("omni.agents.reflection.call_llm", return_value=_proposal_json()) as mock_call:
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    mock_call.assert_called_once()
    assert result.status == "proposed"
    assert result.proposal_id is not None
    proposal = next(p for p in evolution.ledger() if p.id == result.proposal_id)
    assert proposal.domain == "routing"
    assert proposal.status == "proposed"


def test_no_signal_when_model_finds_nothing_worth_proposing():
    policy, memory, orchestrator, evolution = _setup()
    no_signal = json.dumps({"has_proposal": False})
    with patch("omni.agents.reflection.call_llm", return_value=no_signal):
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    assert result.status == "no_signal"
    assert evolution.ledger() == []


def test_unparseable_response_fails_without_crashing():
    policy, memory, orchestrator, evolution = _setup()
    with patch("omni.agents.reflection.call_llm", return_value="not json at all"):
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    assert result.status == "failed"
    assert "parse" in result.error
    assert evolution.ledger() == []


def test_invalid_domain_from_the_model_fails_but_report_is_still_recorded():
    policy, memory, orchestrator, evolution = _setup()
    bad_domain = _proposal_json(domain="not-a-real-domain")
    with patch("omni.agents.reflection.call_llm", return_value=bad_domain):
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    assert result.status == "failed"
    assert evolution.ledger() == []
    # the raw model output made it into versioned memory even though the
    # proposal itself couldn't be created -- nothing is silently lost
    entry = memory.read(f"self_review.{result.run_id}.report")
    assert entry.value["parsed"]["domain"] == "not-a-real-domain"


def test_llm_not_configured_is_reported_not_swallowed():
    policy, memory, orchestrator, evolution = _setup()
    with patch("omni.agents.reflection.call_llm", side_effect=LLMNotConfigured("no key")):
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    assert result.status == "failed"
    assert "no key" in result.error


def test_policy_denial_short_circuits_before_any_llm_call():
    policy = PolicyEngine()  # no seed rules at all -- default deny
    memory = MemoryStore()
    orchestrator = MetaOrchestrator()
    evolution = EvolutionEngine()
    with patch("omni.agents.reflection.call_llm") as mock_call:
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    mock_call.assert_not_called()
    assert result.status == "denied"


def test_snapshot_reflects_real_orchestrator_state():
    policy, memory, orchestrator, evolution = _setup()
    orchestrator.register_agent("Busy Agent", skills=["coding"], agent_type=AgentType.WORKER)
    orchestrator.submit(TaskSpec(name="t1", required_skills=["coding"]))
    with patch("omni.agents.reflection.call_llm", return_value=_proposal_json()):
        result = run_reflection_cycle(policy, memory, orchestrator, evolution)
    assert result.snapshot["agent_count"] == 1
    assert result.snapshot["task_count"] == 1


def test_scheduler_tick_runs_one_real_cycle():
    policy, memory, orchestrator, evolution = _setup()
    bus = InMemoryBus()
    scheduler = ReflectionScheduler(policy, memory, orchestrator, evolution, bus=bus, interval_s=9999)
    with patch("omni.agents.reflection.call_llm", return_value=_proposal_json()):
        result = scheduler.tick()
    assert result.status == "proposed"
    subjects = [s for s, _ in bus.published]
    assert f"learning.{result.run_id}.started" in subjects
    assert f"learning.{result.run_id}.completed" in subjects
