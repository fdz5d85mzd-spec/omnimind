"""POST /agent/run API tests: the public endpoint the consumer chat UI calls."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)


def test_agent_run_without_llm_key_reports_not_configured():
    with patch.dict("os.environ", {}, clear=True):
        r = client.post("/agent/run", json={"prompt": "hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["answer"] is None
    assert "API_KEY" in body["error"]


def test_agent_run_with_llm_configured_returns_answer():
    with patch("omni.agents.runner.call_llm", return_value="Hi there!"):
        r = client.post("/agent/run", json={"prompt": "hello", "session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["answer"] == "Hi there!"
    assert body["run_id"].startswith("run_")


def test_agent_run_event_visible_on_twin_stream():
    with client.websocket_connect("/twin/stream") as ws:
        ws.receive_json()  # initial snapshot
        with patch("omni.agents.runner.call_llm", return_value="42"):
            r = client.post("/agent/run", json={"prompt": "the answer?"})
        run_id = r.json()["run_id"]

        seen_subjects = set()
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "fleet_event":
                seen_subjects.add(msg["subject"])
            if f"agent.{run_id}.completed" in seen_subjects:
                break
        assert f"agent.{run_id}.completed" in seen_subjects


def test_cors_allows_cross_origin_requests():
    r = client.options(
        "/agent/run",
        headers={
            "Origin": "https://omnimind-app.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"
