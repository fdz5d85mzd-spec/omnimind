"""Digital Twin live stream tests: TwinBroadcaster fan-out semantics
(unit) and the /twin/stream WebSocket route end-to-end (integration,
via FastAPI's TestClient websocket support)."""

from omni.twin.stream import TwinBroadcaster


# ------------------------------------------------------------ TwinBroadcaster
def test_broadcast_delivers_to_all_subscribers():
    hub = TwinBroadcaster()
    a, b = [], []
    hub.subscribe(a.append)
    hub.subscribe(b.append)
    delivered = hub.broadcast({"type": "x"})
    assert delivered == 2
    assert a == [{"type": "x"}]
    assert b == [{"type": "x"}]


def test_unsubscribe_stops_delivery():
    hub = TwinBroadcaster()
    received = []
    sub_id = hub.subscribe(received.append)
    hub.broadcast({"n": 1})
    hub.unsubscribe(sub_id)
    hub.broadcast({"n": 2})
    assert received == [{"n": 1}]


def test_broadcast_with_no_subscribers_returns_zero():
    hub = TwinBroadcaster()
    assert hub.broadcast({"type": "x"}) == 0


def test_raising_sink_is_pruned_without_breaking_other_deliveries():
    hub = TwinBroadcaster()
    received = []

    def bad_sink(message):
        raise RuntimeError("connection died")

    hub.subscribe(bad_sink)
    hub.subscribe(received.append)
    assert hub.subscriber_count() == 2

    delivered = hub.broadcast({"type": "x"})
    assert delivered == 1  # only the good sink
    assert received == [{"type": "x"}]
    assert hub.subscriber_count() == 1  # bad sink was pruned

    # a second broadcast still works fine — nothing left broken
    hub.broadcast({"type": "y"})
    assert received == [{"type": "x"}, {"type": "y"}]


def test_subscriber_count_reflects_active_subscriptions():
    hub = TwinBroadcaster()
    assert hub.subscriber_count() == 0
    id1 = hub.subscribe(lambda m: None)
    id2 = hub.subscribe(lambda m: None)
    assert hub.subscriber_count() == 2
    hub.unsubscribe(id1)
    assert hub.subscriber_count() == 1
    hub.unsubscribe(id2)
    assert hub.subscriber_count() == 0


# ------------------------------------------------------------ /twin/stream (integration)
def test_stream_sends_initial_snapshot():
    from omni.api.main import app

    from fastapi.testclient import TestClient

    client = TestClient(app)
    with client.websocket_connect("/twin/stream") as ws:
        message = ws.receive_json()
        assert message["type"] == "snapshot"
        assert "agents" in message["data"]
        assert "counters" in message["data"]


def test_stream_forwards_fleet_events_live():
    from omni.api.main import app

    from fastapi.testclient import TestClient

    client = TestClient(app)
    with client.websocket_connect("/twin/stream") as ws:
        ws.receive_json()  # initial snapshot, not under test here

        r = client.post("/fleet/enqueue", json={"name": "stream-test-job"})
        assert r.status_code == 200

        message = ws.receive_json()
        assert message["type"] == "fleet_event"
        assert message["subject"] == "fleet.queue.enqueued"
        assert message["payload"]["name"] == "stream-test-job"


def test_subscriber_count_endpoint_reflects_open_connections():
    from omni.api.main import app

    from fastapi.testclient import TestClient

    client = TestClient(app)
    before = client.get("/twin/stream/subscribers").json()["connected"]
    with client.websocket_connect("/twin/stream") as ws:
        ws.receive_json()  # drain the initial snapshot so the subscribe has landed
        during = client.get("/twin/stream/subscribers").json()["connected"]
        assert during == before + 1
    after = client.get("/twin/stream/subscribers").json()["connected"]
    assert after == before
