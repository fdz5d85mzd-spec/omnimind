"""Meta-Orchestrator tests: assignment, balancing, bottleneck & duplicate
detection, auto spawn/terminate, prediction, model selection."""

from omni.contracts.agent import AgentStatus, TaskSpec
from omni.orchestrator.engine import MetaOrchestrator


def test_assign_picks_capable_agent():
    o = MetaOrchestrator()
    o.register_agent("generalist", skills=["summarize"])
    expert = o.register_agent("expert", skills=["summarize", "code"])
    t = o.submit(TaskSpec(name="build", required_skills=["summarize", "code"]))
    assert o.assign(t.id) == expert.id
    assert expert.queue_depth == 1


def test_assign_balances_by_load():
    o = MetaOrchestrator()
    a = o.register_agent("a")
    b = o.register_agent("b")
    a.load = 0.9
    t = o.submit(TaskSpec(name="t"))
    assert o.assign(t.id) == b.id


def test_no_capable_agent_returns_none():
    o = MetaOrchestrator()
    o.register_agent("only-summarize", skills=["summarize"])
    t = o.submit(TaskSpec(name="needs-code", required_skills=["code"]))
    assert o.assign(t.id) is None


def test_bottleneck_detection():
    o = MetaOrchestrator()
    a = o.register_agent("busy")
    a.load = 0.9
    b = o.register_agent("queued")
    b.queue_depth = 11
    found = {v.agent_id for v in o.detect_bottlenecks()}
    assert found == {a.id, b.id}


def test_duplicate_work_detection():
    o = MetaOrchestrator()
    o.submit(TaskSpec(name="x", signature="sig1"))
    o.submit(TaskSpec(name="y", signature="sig1"))
    o.submit(TaskSpec(name="z", signature="sig2"))
    dup = o.detect_duplicates()
    assert len(dup) == 1
    assert len(dup[0]) == 2


def test_spawn_and_terminate():
    o = MetaOrchestrator(keep_min_idle=0, idle_ttl_s=0)
    a = o.register_agent("w1")
    a.load = 0.95
    spawned = o.spawn_if_needed()
    assert len(spawned) == 1  # surge → new agent
    a.status = AgentStatus.IDLE
    terminated = o.terminate_idle()
    assert a.id in terminated  # idle beyond TTL → terminated


def test_balance_reassigns_tasks():
    o = MetaOrchestrator()
    a = o.register_agent("hot")
    b = o.register_agent("cold")
    t = o.submit(TaskSpec(name="t"))
    o.assign(t.id)  # goes to 'a' (first, equal score)
    a.load = 0.9
    b.load = 0.0
    moves = o.balance()
    assert len(moves) == 1
    assert moves[0].from_agent == a.id
    assert moves[0].to_agent == b.id


def test_prediction_and_model_selection():
    o = MetaOrchestrator()
    for _ in range(3):
        o.submit(TaskSpec(name="t"))
    assert o.predict_next_tasks(1) >= 0.0
    t = TaskSpec(name="m")
    assert o.choose_model(t, "cost").model == "gemini-2-flash"
    assert o.choose_model(t, "quality").quality == 0.98
    assert o.choose_model(t, "speed").latency_ms == 350


def test_report_shape():
    o = MetaOrchestrator()
    o.register_agent("w1")
    o.submit(TaskSpec(name="t"))
    report = o.report()
    assert report["agents_total"] == 1
    assert report["tasks_total"] == 1
    assert "predicted_tasks_next_10m" in report
    assert "bottlenecks" in report
    assert report["tasks_failed"] == 0


def test_report_counts_failed_tasks_separately_from_completed():
    o = MetaOrchestrator()
    o.register_agent("w1")
    ok = o.submit(TaskSpec(name="ok"))
    bad = o.submit(TaskSpec(name="bad"))
    o.assign(ok.id)
    o.assign(bad.id)
    o.complete(ok.id, result={"answer": "done"})
    o.fail(bad.id, error="boom")
    report = o.report()
    assert report["tasks_completed"] == 1
    assert report["tasks_failed"] == 1
