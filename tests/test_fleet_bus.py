"""Fleet message bus tests (M8+): InMemoryBus pub/sub semantics and
FleetNode integration — real-time events alongside the existing polling
storage. NatsBus is not exercised here (needs a live NATS server, same
policy as PostgresFleetStorage); `make_bus` and the API's connect-or-fall-back
wiring are covered structurally."""

from omni.fleet.bus import InMemoryBus, make_bus
from omni.fleet.node import FleetNode
from omni.fleet.storage import InMemoryFleetStorage


def test_publish_delivers_to_subscriber():
    bus = InMemoryBus()
    received = []
    bus.subscribe("fleet.node-a.announce", lambda subject, payload: received.append((subject, payload)))
    bus.publish("fleet.node-a.announce", {"node": "node-a"})
    assert received == [("fleet.node-a.announce", {"node": "node-a"})]


def test_publish_before_subscribe_is_not_delivered_retroactively():
    bus = InMemoryBus()
    bus.publish("fleet.x.announce", {"a": 1})
    received = []
    bus.subscribe("fleet.x.announce", lambda s, p: received.append(p))
    bus.publish("fleet.x.announce", {"a": 2})
    assert received == [{"a": 2}]  # only the second publish, not replayed history


def test_multiple_subscribers_all_receive():
    bus = InMemoryBus()
    a, b = [], []
    bus.subscribe("fleet.leader.elected", lambda s, p: a.append(p))
    bus.subscribe("fleet.leader.elected", lambda s, p: b.append(p))
    bus.publish("fleet.leader.elected", {"leader": "node-1"})
    assert a == [{"leader": "node-1"}]
    assert b == [{"leader": "node-1"}]


def test_unsubscribe_stops_delivery():
    bus = InMemoryBus()
    received = []
    sub = bus.subscribe("fleet.queue.enqueued", lambda s, p: received.append(p))
    bus.publish("fleet.queue.enqueued", {"i": 1})
    sub.unsubscribe()
    bus.publish("fleet.queue.enqueued", {"i": 2})
    assert received == [{"i": 1}]


def test_wildcard_star_matches_one_token():
    bus = InMemoryBus()
    received = []
    bus.subscribe("fleet.*.announce", lambda s, p: received.append(s))
    bus.publish("fleet.node-a.announce", {})
    bus.publish("fleet.node-b.announce", {})
    bus.publish("fleet.node-a.other", {})  # wrong final token — no match
    assert received == ["fleet.node-a.announce", "fleet.node-b.announce"]


def test_wildcard_gt_matches_rest_of_subject():
    bus = InMemoryBus()
    received = []
    bus.subscribe("fleet.>", lambda s, p: received.append(s))
    bus.publish("fleet.node-a.announce", {})
    bus.publish("fleet.leader.elected", {})
    bus.publish("other.subject", {})  # different root — no match
    assert received == ["fleet.node-a.announce", "fleet.leader.elected"]


def test_published_audit_trail_records_every_event():
    bus = InMemoryBus()
    bus.publish("fleet.a.announce", {"x": 1})
    bus.publish("fleet.b.announce", {"x": 2})
    assert bus.published == [("fleet.a.announce", {"x": 1}), ("fleet.b.announce", {"x": 2})]


def test_make_bus_without_url_returns_in_memory():
    bus = make_bus(None)
    assert isinstance(bus, InMemoryBus)


# ------------------------------------------------------------ FleetNode integration
def test_node_without_bus_still_works():
    """Backward compatible: bus is optional, existing polling-only usage is unaffected."""
    node = FleetNode(storage=InMemoryFleetStorage(), node_id_value="node-solo")
    result = node.announce()
    assert result["node"] == "node-solo"


def test_announce_publishes_event():
    bus = InMemoryBus()
    node = FleetNode(storage=InMemoryFleetStorage(), node_id_value="node-a", bus=bus)
    received = []
    bus.subscribe("fleet.node-a.announce", lambda s, p: received.append(p))
    node.announce()
    assert len(received) == 1
    assert received[0]["node"] == "node-a"


def test_election_publishes_leader_elected_only_for_winner():
    storage = InMemoryFleetStorage()
    bus = InMemoryBus()
    a = FleetNode(storage=storage, node_id_value="node-a", capacity=4, bus=bus)
    b = FleetNode(storage=storage, node_id_value="node-b", capacity=8, bus=bus)
    b.announce()  # both nodes must be candidates before the election is meaningful
    events = []
    bus.subscribe("fleet.leader.elected", lambda s, p: events.append(p))
    a.elect()  # 'b' has higher capacity and wins; 'a' is not the winner
    assert events == []  # loser does not publish
    b.elect()
    assert len(events) == 1
    assert events[0]["leader"] == "node-b"


def test_enqueue_and_adopt_publish_events():
    bus = InMemoryBus()
    node = FleetNode(storage=InMemoryFleetStorage(), node_id_value="node-a", bus=bus)
    enqueued, leased = [], []
    bus.subscribe("fleet.queue.enqueued", lambda s, p: enqueued.append(p))
    bus.subscribe("fleet.queue.leased", lambda s, p: leased.append(p))
    node.enqueue({"name": "job-1"})
    assert len(enqueued) == 1 and enqueued[0]["name"] == "job-1"
    node.adopt_task()
    assert len(leased) == 1 and leased[0]["name"] == "job-1" and leased[0]["node"] == "node-a"


def test_adopt_with_empty_queue_publishes_nothing():
    bus = InMemoryBus()
    node = FleetNode(storage=InMemoryFleetStorage(), node_id_value="node-a", bus=bus)
    events = []
    bus.subscribe("fleet.queue.leased", lambda s, p: events.append(p))
    assert node.adopt_task() is None
    assert events == []
