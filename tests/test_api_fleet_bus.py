"""Fleet message bus API tests (M8+): /fleet/bus/status, /fleet/bus/events,
and that the existing /fleet/* endpoints now emit bus events end-to-end
through the live app instance (default in-memory backend, no NATS_URL set
in the test environment)."""

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)


def test_bus_status_defaults_to_in_memory():
    r = client.get("/fleet/bus/status")
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "in_memory"
    assert body["fallback_reason"] is None


def test_announce_is_visible_in_bus_events():
    client.post("/fleet/announce")
    r = client.get("/fleet/bus/events", params={"limit": 10})
    assert r.status_code == 200
    events = r.json()
    assert any(e["subject"].endswith(".announce") for e in events)


def test_enqueue_is_visible_in_bus_events():
    client.post("/fleet/enqueue", json={"name": "bus-test-job"})
    r = client.get("/fleet/bus/events", params={"limit": 50})
    events = r.json()
    matches = [e for e in events if e["subject"] == "fleet.queue.enqueued" and e["payload"].get("name") == "bus-test-job"]
    assert len(matches) == 1
