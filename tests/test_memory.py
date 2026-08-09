"""Versioned Memory tests: immutability, diff, rollback, branching,
snapshots and time travel."""

from omni.contracts.memory import BranchRequest, RollbackRequest
from omni.memory.store import MemoryStore


def test_write_and_read_are_immutable():
    s = MemoryStore()
    s.write("routing", {"region": "eu"}, "agt_1", "initial")
    s.write("routing", {"region": "us"}, "agt_1", "region change")
    latest = s.read("routing")
    assert latest.version == 2
    assert latest.value == {"region": "us"}
    # the past is never rewritten
    assert s.read("routing", version=1).value == {"region": "eu"}
    assert len(s.history("routing")) == 2
    # who/what/why metadata is recorded
    assert s.read("routing", version=1).agent_id == "agt_1"
    assert s.read("routing").reason == "region change"


def test_structural_diff():
    s = MemoryStore()
    s.write("cfg", {"a": 1, "b": 2}, "agt_1", "v1")
    s.write("cfg", {"a": 1, "c": 3}, "agt_1", "v2")
    d = s.diff("cfg", from_version=1, to_version=2)
    assert d is not None
    ops = {(i.op.value, i.path) for i in d.items}
    assert ("add", "c") in ops
    assert ("remove", "b") in ops


def test_nested_diff_paths():
    s = MemoryStore()
    s.write("cfg", {"a": 1, "nested": {"x": 1}}, "agt_1", "v1")
    s.write("cfg", {"a": 2, "nested": {"x": 2}}, "agt_1", "v2")
    d = s.diff("cfg", from_version=1, to_version=2)
    ops = {(i.op.value, i.path) for i in d.items}
    assert ("replace", "a") in ops
    assert ("replace", "nested.x") in ops


def test_rollback_appends_new_version():
    s = MemoryStore()
    s.write("r", {"v": 1}, "agt_1", "v1")
    s.write("r", {"v": 2}, "agt_1", "v2")
    entry = s.rollback(RollbackRequest(key="r", target_version=1, agent_id="agt_1", reason="revert"))
    assert entry.version == 3
    assert entry.value == {"v": 1}
    assert entry.parent_version == 2
    assert len(s.history("r")) == 3  # nothing was deleted


def test_branching_isolates_states():
    s = MemoryStore()
    s.write("r", {"v": 1}, "agt_1", "v1")
    s.write("r", {"v": 2}, "agt_1", "v2")
    b = s.branch(
        BranchRequest(
            key="r", source_branch="main", source_version=1,
            new_branch="exp", agent_id="agt_1", reason="branch experiment",
        )
    )
    assert b.version == 1
    assert b.value == {"v": 1}
    s.write("r", {"v": 99}, "agt_1", "exp change", branch="exp")
    assert s.read("r", branch="exp").value == {"v": 99}
    assert s.read("r", branch="main").value == {"v": 2}  # main untouched


def test_snapshot_and_time_travel():
    s = MemoryStore()
    s.write("a", {"x": 1}, "agt_1", "v1")
    s.write("b", {"y": 1}, "agt_1", "v1")
    snap = s.snapshot()
    assert set(snap) == {"a", "b"}
    assert snap["a"]["main"] == {"x": 1}
    # time travel: before anything existed
    assert s.snapshot_asof("2000-01-01T00:00:00") == {}
    # time travel: after everything
    assert set(s.snapshot_asof("2100-01-01T00:00:00")) == {"a", "b"}
