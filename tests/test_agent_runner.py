"""AgentRunner tests: the public "ask anything" entry point wired through
Policy Engine, Meta-Orchestrator, Versioned Memory, and the fleet bus."""

from unittest.mock import patch

from omni.agents.llm import LLMError, LLMNotConfigured
from omni.agents.runner import AgentRunner
from omni.fleet.bus import InMemoryBus
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine, make_seed_rules


def _runner(bus=None):
    policy = PolicyEngine(make_seed_rules())
    memory = MemoryStore()
    orchestrator = MetaOrchestrator()
    return AgentRunner(policy=policy, memory=memory, orchestrator=orchestrator, bus=bus), memory, orchestrator


def test_run_without_llm_configured_fails_cleanly():
    runner, _, _ = _runner()
    with patch.dict("os.environ", {}, clear=True):
        result = runner.run("what is the capital of France?")
    assert result.status == "failed"
    assert "OPENAI_API_KEY" in result.error or "ANTHROPIC_API_KEY" in result.error
    assert result.answer is None


def test_run_with_llm_configured_returns_real_answer():
    runner, memory, orchestrator = _runner()
    with patch("omni.agents.runner.call_llm", return_value="Paris.") as mock_call:
        result = runner.run("what is the capital of France?", session_id="s1")
    mock_call.assert_called_once()
    assert result.status == "completed"
    assert result.answer == "Paris."
    assert result.task_id is not None

    # the exchange is really recorded in versioned memory
    assert memory.read(f"agent_run.{result.run_id}.prompt").value["prompt"] == "what is the capital of France?"
    assert memory.read(f"agent_run.{result.run_id}.answer").value["answer"] == "Paris."

    # a real task went through the orchestrator, not a fake status
    task = next(t for t in orchestrator.tasks() if t.id == result.task_id)
    assert task.status.value == "completed"
    assert task.result == {"answer": "Paris."}


def test_run_publishes_live_stages_to_the_bus():
    bus = InMemoryBus()
    runner, _, _ = _runner(bus=bus)
    with patch("omni.agents.runner.call_llm", return_value="42"):
        result = runner.run("the answer to everything?", session_id="alice")
    subjects = [s for s, _ in bus.published]
    assert f"agent.{result.run_id}.started" in subjects
    assert f"agent.{result.run_id}.policy_evaluated" in subjects
    assert f"agent.{result.run_id}.memory_stored" in subjects
    assert f"agent.{result.run_id}.task_assigned" in subjects
    assert f"agent.{result.run_id}.thinking" in subjects
    assert f"agent.{result.run_id}.completed" in subjects
    # every event carries the session_id so a multi-user stream can be
    # filtered client-side to just the caller's own run
    assert all(payload["session_id"] == "alice" for _, payload in bus.published)


def test_concurrent_sessions_are_distinguishable_on_the_bus():
    bus = InMemoryBus()
    runner, _, _ = _runner(bus=bus)
    with patch("omni.agents.runner.call_llm", side_effect=["a1", "a2"]):
        r1 = runner.run("q1", session_id="alice")
        r2 = runner.run("q2", session_id="bob")
    alice_events = [(s, p) for s, p in bus.published if p["session_id"] == "alice"]
    bob_events = [(s, p) for s, p in bus.published if p["session_id"] == "bob"]
    assert all(r1.run_id in s for s, _ in alice_events)
    assert all(r2.run_id in s for s, _ in bob_events)
    assert r1.run_id != r2.run_id


def test_llm_error_is_reported_not_swallowed():
    runner, _, orchestrator = _runner()
    with patch("omni.agents.runner.call_llm", side_effect=LLMError("provider returned 500")):
        result = runner.run("hello")
    assert result.status == "failed"
    assert "500" in result.error
    task = next(t for t in orchestrator.tasks() if t.id == result.task_id)
    assert task.status.value == "failed" or task.result == {"error": result.error}


def test_two_sessions_get_independent_run_ids_and_memory_keys():
    runner, memory, _ = _runner()
    with patch("omni.agents.runner.call_llm", side_effect=["a1", "a2"]):
        r1 = runner.run("q1", session_id="alice")
        r2 = runner.run("q2", session_id="bob")
    assert r1.run_id != r2.run_id
    assert memory.read(f"agent_run.{r1.run_id}.answer").value["answer"] == "a1"
    assert memory.read(f"agent_run.{r2.run_id}.answer").value["answer"] == "a2"
