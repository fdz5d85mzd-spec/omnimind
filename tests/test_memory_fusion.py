"""Cross-session knowledge fusion tests: newest / merge strategies,
source-branch immutability."""

import time

import pytest

from omni.memory.fusion import MemoryFuser
from omni.memory.store import MemoryStore


def test_newest_strategy_wins_by_creation_time():
    store = MemoryStore()
    store.write("k", {"v": 1}, "ag", "seed", branch="exp1")
    time.sleep(0.02)
    store.write("k", {"v": 2}, "ag", "seed", branch="exp2")

    report = MemoryFuser(store).fuse(["exp1", "exp2"], target_branch="main")
    assert store.read("k").value == {"v": 2}
    assert report.keys_fused == 1
    assert report.decisions[0]["winner"] == "exp2@1"


def test_merge_strategy_combines_nested_values():
    store = MemoryStore()
    store.write("knowledge", {"a": {"x": 1}, "b": 2}, "ag", "r", branch="exp1")
    store.write("knowledge", {"a": {"y": 3}, "c": 4}, "ag", "r", branch="exp2")

    MemoryFuser(store, strategy="merge").fuse(["exp1", "exp2"], target_branch="main")
    value = store.read("knowledge").value
    assert value == {"a": {"x": 1, "y": 3}, "b": 2, "c": 4}


def test_source_branches_never_touched():
    store = MemoryStore()
    store.write("k", {"v": 1}, "ag", "r", branch="exp1")

    MemoryFuser(store).fuse(["exp1"], target_branch="main")
    assert store.read("k", "exp1").value == {"v": 1}
    assert store.read("k").value == {"v": 1}
    assert store.read("k", "main").version == 1


def test_unchanged_keys_are_not_rewritten():
    store = MemoryStore()
    store.write("k", {"v": 1}, "ag", "r", branch="exp1")
    fuser = MemoryFuser(store)
    first = fuser.fuse(["exp1"], target_branch="main")
    assert first.keys_fused == 1
    second = fuser.fuse(["exp1"], target_branch="main")
    assert second.keys_fused == 0
    assert second.keys_unchanged == 1
    assert store.read("k", "main").version == 1  # no extra versions


def test_invalid_strategy_and_empty_sources_rejected():
    store = MemoryStore()
    with pytest.raises(KeyError):
        MemoryFuser(store, strategy="merge_everything")
    with pytest.raises(ValueError):
        MemoryFuser(store).fuse([], target_branch="main")
