"""AgentRunner tests: the public "ask anything" entry point wired through
Policy Engine, Meta-Orchestrator, Versioned Memory, and the fleet bus."""

from unittest.mock import patch

from omni.agents.fleet_seed import seed_fleet
from omni.agents.llm import LLMError, LLMNotConfigured
from omni.agents.runner import AgentRunner
from omni.fleet.bus import InMemoryBus
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine, make_seed_rules


def _runner(bus=None, seeded=False):
    policy = PolicyEngine(make_seed_rules())
    memory = MemoryStore()
    orchestrator = MetaOrchestrator()
    if seeded:
        seed_fleet(orchestrator)
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
    assert task.status.value == "failed"
    assert task.result == {"error": result.error}


def test_two_sessions_get_independent_run_ids_and_memory_keys():
    runner, memory, _ = _runner()
    with patch("omni.agents.runner.call_llm", side_effect=["a1", "a2"]):
        r1 = runner.run("q1", session_id="alice")
        r2 = runner.run("q2", session_id="bob")
    assert r1.run_id != r2.run_id
    assert memory.read(f"agent_run.{r1.run_id}.answer").value["answer"] == "a1"
    assert memory.read(f"agent_run.{r2.run_id}.answer").value["answer"] == "a2"


# ------------------------------------------------------------ run_stream()
def test_run_stream_yields_deltas_then_done():
    runner, memory, orchestrator = _runner()
    with patch("omni.agents.runner.stream_llm", return_value=iter(["Par", "is."])):
        events = list(runner.run_stream("capital of France?", session_id="s1"))

    delta_events = [e for e in events if e["type"] == "delta"]
    assert [e["text"] for e in delta_events] == ["Par", "is."]

    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "Paris."
    assert memory.read(f"agent_run.{done['run_id']}.answer").value["answer"] == "Paris."
    task = next(t for t in orchestrator.tasks() if t.id is not None and t.result == {"answer": "Paris."})
    assert task.status.value == "completed"


def test_run_stream_without_llm_configured_yields_failed_not_fake_text():
    runner, _, _ = _runner()
    with patch.dict("os.environ", {}, clear=True):
        events = list(runner.run_stream("hello"))
    assert all(e["type"] != "delta" for e in events)  # never fabricates partial output
    assert events[-1]["type"] == "failed"
    assert "API_KEY" in events[-1]["error"]


def test_run_stream_denied_short_circuits_before_any_llm_call():
    policy = PolicyEngine(make_seed_rules())
    memory = MemoryStore()
    orchestrator = MetaOrchestrator()
    runner = AgentRunner(policy=policy, memory=memory, orchestrator=orchestrator)
    with patch("omni.agents.runner.stream_llm") as mock_stream:
        with patch.object(policy, "evaluate") as mock_eval:
            mock_eval.return_value.allowed = False
            mock_eval.return_value.reason = "blocked for test"
            events = list(runner.run_stream("hello"))
    mock_stream.assert_not_called()
    assert events == [{"type": "denied", "run_id": events[0]["run_id"], "error": "blocked for test"}]


def test_run_stream_publishes_bus_events_matching_delta_order():
    bus = InMemoryBus()
    runner, _, _ = _runner(bus=bus)
    with patch("omni.agents.runner.stream_llm", return_value=iter(["4", "2"])):
        events = list(runner.run_stream("the answer?", session_id="alice"))
    run_id = events[-1]["run_id"]
    subjects = [s for s, _ in bus.published]
    assert f"agent.{run_id}.thinking" in subjects
    assert f"agent.{run_id}.completed" in subjects
    completed_payload = next(p for s, p in bus.published if s == f"agent.{run_id}.completed")
    assert completed_payload["answer"] == "42"


# ------------------------------------------------ real fleet skill routing
def test_coding_prompt_is_routed_to_the_code_agent():
    runner, _, orchestrator = _runner(seeded=True)
    with patch("omni.agents.runner.call_llm", return_value="here's the fix") as mock_call:
        result = runner.run("debug this python function for me", session_id="s1")
    assert result.agent_name == "Code Agent"
    # the specialist's own framing line actually reached the LLM call, not
    # just a name attached after the fact
    system_prompt = mock_call.call_args.kwargs["system"]
    assert "Code Agent" in system_prompt
    # a SPECIALIST does the actual product work on the production model tier
    assert mock_call.call_args.kwargs["model"] == "claude-sonnet-5"

    code_agent = next(a for a in orchestrator.agents() if a.name == "Code Agent")
    assert code_agent.queue_depth == 0  # released after complete()


def test_routine_worker_agent_runs_on_the_cheap_model_tier():
    # "summary of" only matches the summarization pattern (not "writing",
    # which "summarize this article" would also trip) -- routes to the
    # Summarizer, a WORKER not a SPECIALIST
    runner, _, orchestrator = _runner(seeded=True)
    with patch("omni.agents.runner.call_llm", return_value="tl;dr") as mock_call:
        result = runner.run("give me a summary of this", session_id="s1")
    assert result.agent_name == "Summarizer"
    agent = next(a for a in orchestrator.agents() if a.name == "Summarizer")
    assert agent.agent_type.value == "worker"
    assert mock_call.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_research_prompt_is_routed_to_the_research_agent():
    runner, _, _ = _runner(seeded=True)
    with patch("omni.agents.runner.call_llm", return_value="findings") as mock_call:
        result = runner.run("research the causes of the 1929 crash", session_id="s1")
    assert result.agent_name == "Research Agent"
    assert "Research Agent" in mock_call.call_args.kwargs["system"]


def test_unrouted_prompt_still_completes_via_some_capable_agent():
    runner, _, _ = _runner(seeded=True)
    with patch("omni.agents.runner.call_llm", return_value="hi!"):
        result = runner.run("hello there", session_id="s1")
    assert result.status == "completed"
    assert result.agent_name  # some real fleet agent handled it, not None

    # the 40-agent roster is reused across requests instead of growing one
    # disposable "web-agent-*" per message
    fresh_orchestrator = MetaOrchestrator()
    seed_fleet(fresh_orchestrator)
    before = len(fresh_orchestrator.agents())
    policy = PolicyEngine(make_seed_rules())
    memory = MemoryStore()
    runner2 = AgentRunner(policy=policy, memory=memory, orchestrator=fresh_orchestrator)
    with patch("omni.agents.runner.call_llm", return_value="ok"):
        runner2.run("hello")
        runner2.run("hello again")
    assert len(fresh_orchestrator.agents()) == before


def test_falls_back_to_a_disposable_worker_when_fleet_is_not_seeded():
    # no seed_fleet() call -- orchestrator starts with zero agents, exactly
    # like every other test in this file predating fleet-aware routing
    runner, _, orchestrator = _runner(seeded=False)
    with patch("omni.agents.runner.call_llm", return_value="ok"):
        result = runner.run("debug this code", session_id="s1")
    assert result.status == "completed"
    assert result.agent_name is not None
    assert len(orchestrator.agents()) == 1
