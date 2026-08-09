"""OmniMind SDK tests — all client methods against a fake transport."""

from omni.sdk import OmniClient, OmniApiError


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses: dict[str, dict | list] = {}

    def __call__(self, method: str, path: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        if path.startswith("/policy/evaluate"):
            return {"allowed": True, "matched_rule": "rule_allow_agent_ops"}
        if path.startswith("/memory/write"):
            return {"key": payload["key"], "version": 3}
        if path.startswith("/memory/read") or path.startswith("/memory/history"):
            return {"key": "routing.policy", "branch": "main", "version": 3}
        if path.startswith("/tasks/submit"):
            return {"id": "task_abc", "name": payload["name"]}
        if path.startswith("/tasks/"):
            return {"task_id": "task_abc", "assignee": "agt_1"}
        if path.startswith("/marketplace/install"):
            return {"id": "skill_x", "name": "summarize"}
        if path.startswith("/simulation/run"):
            return {"confidence": 0.8, "needs_approval": False}
        if path.startswith("/twin/snapshot"):
            return {"counters": {"agents_total": 1}}
        if path.startswith("/learning/report"):
            return {"samples": 2}
        if path.startswith("/twin/replay") or path.startswith("/audit/replay"):
            return [{"event": "decision.allow"}]
        raise AssertionError(f"unexpected call {method} {path}")


def _client() -> tuple[OmniClient, FakeTransport]:
    transport = FakeTransport()
    return OmniClient(base_url="http://test", transport=transport), transport


def test_policy_evaluate():
    client, _ = _client()
    decision = client.evaluate_policy(
        {"id": "u1", "roles": ["operator"]}, "agent.spawn", {"type": "agent", "attributes": {"risk_level": "low"}}
    )
    assert decision["allowed"] is True


def test_memory_write_read_history():
    client, transport = _client()
    entry = client.write_memory("routing.policy", {"region": "eu"}, "agt_1", "seed")
    assert entry["version"] == 3
    assert client.read_memory("routing.policy") is not None
    assert client.memory_history("routing.policy") is not None
    methods = {m for m, _, _ in transport.calls}
    assert "POST" in methods and "GET" in methods


def test_task_and_skill_flow():
    client, _ = _client()
    task = client.submit_task("summarize", required_skills=["summarize"], priority=2)
    assert task["id"] == "task_abc"
    assert client.assign_task(task["id"])["assignee"] == "agt_1"
    skill = client.install_skill("skill_x", "agt_1")
    assert skill["name"] == "summarize"


def test_simulation_twin_learning():
    client, _ = _client()
    assert client.simulate("deploy", "deploy", {"size": 0.5})["confidence"] == 0.8
    assert client.twin_snapshot()["counters"]["agents_total"] == 1
    assert client.learning_report()["samples"] == 2


def test_replay_builds_query():
    client, transport = _client()
    events = client.replay(subject="ops-1", subsystem="policy")
    assert events[0]["event"] == "decision.allow"
    path = transport.calls[-1][1]
    assert "subject=ops-1" in path and "subsystem=policy" in path


def test_api_error_raised_with_status():
    def boom(method, path, payload=None):
        raise OmniApiError(500, path, "boom")

    client = OmniClient(base_url="http://test", transport=boom)
    try:
        client.twin_snapshot()
        raise AssertionError("should have raised")
    except OmniApiError as e:
        assert e.status == 500
