"""Cross-session knowledge fusion (M8) — consolidate branched memories.

When agents experiment on branches, the winning knowledge should return to
the trunk. `MemoryFuser` merges any set of source branches into a target
branch, producing a new auditable version per key with a decision record.

Strategies
----------
* ``newest`` — the latest-created version across branches wins per key.
* ``merge``  — deep-merge all branch values per key (override wins on
  conflicts, nested dicts combine recursively).

Source branches are never touched.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from omni.memory.store import MemoryStore

STRATEGIES = ("newest", "merge")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge; override wins on conflicts, nested dicts combine."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class FuseReport:
    target_branch: str
    strategy: str
    keys_fused: int = 0
    keys_unchanged: int = 0
    decisions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return vars(self)


class MemoryFuser:
    def __init__(self, store: MemoryStore, agent_id: str = "fuser", strategy: str = "newest") -> None:
        if strategy not in STRATEGIES:
            raise KeyError(f"unknown fusion strategy '{strategy}' (known: {STRATEGIES})")
        self._store = store
        self._agent_id = agent_id
        self._strategy = strategy
        self._lock = threading.RLock()

    def fuse(self, source_branches: list[str], target_branch: str = "main", reason: str = "") -> FuseReport:
        if not source_branches:
            raise ValueError("at least one source branch is required")
        with self._lock:
            keys: set[str] = set()
            latest: dict[str, Any] = {}
            for branch in source_branches:
                for key in self._store.all_keys(branch):
                    entry = self._store.read(key, branch)
                    if entry is None:
                        continue
                    keys.add(key)
                    latest[(branch, key)] = entry

            report = FuseReport(target_branch=target_branch, strategy=self._strategy)
            for key in sorted(keys):
                entries = [e for (b, k), e in latest.items() if k == key]
                if self._strategy == "newest":
                    winner = max(entries, key=lambda e: (e.created_at, e.version))
                    value = winner.value
                    decision = {"key": key, "winner": f"{winner.branch}@{winner.version}"}
                else:  # merge
                    merged: dict[str, Any] = {}
                    for e in entries:
                        merged = deep_merge(merged, e.value)
                    value = merged
                    decision = {
                        "key": key,
                        "winner": "merge",
                        "sources": [f"{e.branch}@{e.version}" for e in entries],
                    }

                existing = self._store.read(key, target_branch)
                if existing is not None and existing.value == value:
                    report.keys_unchanged += 1
                else:
                    self._store.write(
                        key,
                        value,
                        self._agent_id,
                        f"fusion of {source_branches} into {target_branch}: {reason}".strip(),
                        branch=target_branch,
                    )
                    report.keys_fused += 1
                report.decisions.append(decision)
            return report
