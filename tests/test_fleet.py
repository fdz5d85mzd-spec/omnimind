"""Fleet tests (M8): election, registry, shared queue, workload stats."""

from omni.contracts.agent import TaskSpec
from omni.fleet.node import FleetNode
from omni.fleet.storage import InMemoryFleetStorage
from omni.orchestrator.engine import MetaOrchestrator


def test_announce_and_peers():
    storage = InMemoryFleetStorage()
    a = FleetNode(storage=storage, node_id_value="node-a", capacity=8)
    b = FleetNode(storage=storage, node_id_value="node-b", capacity=8)
    a.announce()
    b.announce()
    assert a.is_registered() is True
    assert {p["node"] for p in a.peers()} == {"node-b"}


def test_election_prefers_highest_capacity_then_id():
    storage = InMemoryFleetStorage()
    small = FleetNode(storage=storage, node_id_value="node-small", capacity=4)
    big = FleetNode(storage=storage, node_id_value="node-big", capacity=16)
    small.announce()
    big.announce()
    assert big.elect()["leader"] == "node-big"


def test_election_tie_breaks_by_node_id():
    storage = InMemoryFleetStorage()
    a = FleetNode(storage=storage, node_id_value="node-a", capacity=8)
    b = FleetNode(storage=storage, node_id_value="node-b", capacity=8)
    a.announce()
    b.announce()
    # lexicographically larger id wins the tie — deterministic from either side
    assert a.elect()["leader"] == "node-b"
    assert a.is_leader() is False
    assert b.elect()["leader"] == "node-b"
    assert b.is_leader() is True


def test_shared_queue_enqueue_and_lease():
    storage = InMemoryFleetStorage()
    node = FleetNode(storage=storage, node_id_value="node-x")
    node.enqueue({"name": "task-1", "required_skills": []})
    node.enqueue({"name": "task-2", "required_skills": []})
    leased = node.adopt_task()
    assert leased["name"] == "task-1"  # FIFO
    assert node.adopt_task()["name"] == "task-2"
    assert node.adopt_task() is None  # drained


def test_workload_stats_derived_from_orchestrator():
    storage = InMemoryFleetStorage()
    node = FleetNode(storage=storage)
    orch = MetaOrchestrator()
    orch.register_agent("w1")
    orch.submit(TaskSpec(name="t"))
    stats = node.workload(orch)
    assert stats.total_agents == 1
    assert stats.total_queued == 1
    assert stats.average_load >= 0.0
