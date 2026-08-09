"""Control-plane API smoke tests (live HTTP requests against FastAPI)."""

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "OmniMind"
    assert "policy" in body["subsystems"]


def test_policy_evaluate_endpoint():
    r = client.post(
        "/policy/evaluate",
        json={
            "principal": {"id": "u1", "roles": ["operator"], "groups": [], "attributes": {}},
            "action": "agent.spawn",
            "resource": {"type": "agent", "attributes": {"risk_level": "low"}},
            "cost": 0.0,
            "calls": 1,
        },
    )
    assert r.status_code == 200
    assert r.json()["allowed"] is True


def test_memory_endpoints():
    r = client.post(
        "/memory/write",
        json={"key": "k", "value": {"v": 1}, "agent_id": "a", "reason": "seed"},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 1
    r = client.get("/memory/read", params={"key": "k"})
    assert r.status_code == 200
    assert r.json()["value"] == {"v": 1}


def test_orchestrator_roundtrip():
    r = client.post("/agents/register", json={"name": "w1"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    r = client.post("/tasks/submit", json={"name": "t", "risk_level": "low"})
    task_id = r.json()["id"]
    r = client.post(f"/tasks/{task_id}/assign")
    assert r.status_code == 200
    assert r.json()["assignee"] == agent_id


def test_marketplace_publish_and_search():
    r = client.post(
        "/marketplace/publish",
        json={
            "name": "summarize",
            "description": "summarize docs",
            "kind": "official",
            "author": "core",
            "version": "1.0.0",
        },
    )
    assert r.status_code == 200
    assert r.json()["latest_version"] == "1.0.0"
    r = client.get("/marketplace/search", params={"query": "summ"})
    assert any(s["name"] == "summarize" for s in r.json())


def test_simulation_run_endpoint():
    r = client.post(
        "/simulation/run",
        json={"action": "deploy", "domain": "deploy", "params": {"size": 0.5}},
    )
    assert r.status_code == 200
    assert r.json()["needs_approval"] is False
    assert r.json()["confidence"] >= 0.5


def test_twin_snapshot_endpoint():
    r = client.get("/twin/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["agents"], list)
    assert "counters" in body


def test_learning_ingest_and_report():
    r = client.post(
        "/learning/ingest",
        json={
            "task_id": "t1",
            "agent_id": "a1",
            "task_type": "summary",
            "metrics": {"accuracy": 0.8, "cost": 0.1, "execution_time_ms": 100},
        },
    )
    assert r.status_code == 200
    r = client.get("/learning/report")
    assert r.status_code == 200
    assert r.json()["samples"] >= 1
