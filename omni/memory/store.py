"""Immutable, append-only versioned memory store (SQLite).

Invariants
----------
* `(key, branch, version)` is the primary key — versions are never overwritten.
* Every write records `agent_id` (WHO), `reason` (WHY), `created_at` (WHEN).
* Rollback *appends* a new version whose value copies the target version; the
  past is never rewritten.
* Branching copies a version into a new branch as v1 with `provenance`.
* A full-store snapshot can be taken and later replayed via `restore_snapshot`
  (time travel to any prior state).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omni.contracts.memory import (
    BranchRequest,
    MemoryDiff,
    MemoryEntry,
    MemoryDiffItem,
    RollbackRequest,
)
from omni.memory.diff import diff_values


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_versions (
    key TEXT NOT NULL,
    branch TEXT NOT NULL,
    version INTEGER NOT NULL,
    value TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    parent_version INTEGER,
    value_hash TEXT NOT NULL,
    provenance TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (key, branch, version)
);
CREATE INDEX IF NOT EXISTS idx_mem_key ON memory_versions (key, branch, version DESC);
"""


class MemoryStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------- write path
    def write(
        self,
        key: str,
        value: dict[str, Any],
        agent_id: str,
        reason: str,
        branch: str = "main",
    ) -> MemoryEntry:
        with self._lock:
            latest = self._latest(key, branch)
            version = 1 if latest is None else latest.version + 1
            entry = MemoryEntry(
                key=key,
                branch=branch,
                version=version,
                value=value,
                agent_id=agent_id,
                reason=reason,
                parent_version=None if latest is None else latest.version,
                hash=_hash(value),
                created_at=_now(),
            )
            self._insert(entry, provenance=None)
            return entry

    def read(self, key: str, branch: str = "main", version: int | None = None) -> MemoryEntry | None:
        with self._lock:
            if version is None:
                return self._latest(key, branch)
            row = self._conn.execute(
                "SELECT key, branch, version, value, agent_id, reason, parent_version, "
                "value_hash, provenance, created_at FROM memory_versions "
                "WHERE key=? AND branch=? AND version=?",
                (key, branch, version),
            ).fetchone()
            return self._row_to_entry(row)

    def history(self, key: str, branch: str = "main") -> list[MemoryEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, branch, version, value, agent_id, reason, parent_version, "
                "value_hash, provenance, created_at FROM memory_versions "
                "WHERE key=? AND branch=? ORDER BY version ASC",
                (key, branch),
            ).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def diff(self, key: str, branch: str = "main", from_version: int | None = None, to_version: int | None = None) -> MemoryDiff | None:
        with self._lock:
            latest = self._latest(key, branch)
            if latest is None:
                return None
            to_version = to_version or latest.version
            from_version = from_version or max(1, to_version - 1)
            a = self.read(key, branch, from_version)
            b = self.read(key, branch, to_version)
            if a is None or b is None:
                return None
            return MemoryDiff(
                key=key,
                branch=branch,
                from_version=from_version,
                to_version=to_version,
                items=diff_values(a.value, b.value),
            )

    # ------------------------------------------------------------- consume path
    def rollback(self, request: RollbackRequest) -> MemoryEntry:
        """Append a new version equal to an older one. The past is untouched."""
        with self._lock:
            target = self.read(request.key, request.branch, request.target_version)
            if target is None:
                raise KeyError(
                    f"no version {request.target_version} for '{request.key}' on '{request.branch}'"
                )
            entry = self.write(
                request.key,
                target.value,
                request.agent_id,
                f"rollback to v{request.target_version}: {request.reason}",
                request.branch,
            )
            return entry

    def branch(self, request: BranchRequest) -> MemoryEntry:
        """Copy a version into a new branch as v1, recording provenance."""
        with self._lock:
            source = self.read(request.key, request.source_branch, request.source_version)
            if source is None:
                raise KeyError(
                    f"source version not found for '{request.key}'@{request.source_branch}"
                )
            existing = self.read(request.key, request.new_branch, 1)
            if existing is not None:
                raise KeyError(f"branch '{request.new_branch}' already has v1 for '{request.key}'")
            entry = MemoryEntry(
                key=request.key,
                branch=request.new_branch,
                version=1,
                value=source.value,
                agent_id=request.agent_id,
                reason=request.reason,
                parent_version=None,
                hash=source.hash,
                created_at=_now(),
            )
            self._insert(entry, provenance=f"{request.source_branch}@{source.version}")
            return entry

    # ------------------------------------------------------------- snapshots / time travel
    def snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        """{key: {branch: value}} of every latest version."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, branch, value FROM memory_versions v WHERE version = ("
                "SELECT MAX(version) FROM memory_versions v2 "
                "WHERE v2.key = v.key AND v2.branch = v.branch)"
            ).fetchall()
            out: dict[str, dict[str, dict[str, Any]]] = {}
            for key, branch, value in rows:
                out.setdefault(key, {})[branch] = json.loads(value)
            return out

    def snapshot_asof(self, iso_ts: str) -> dict[str, dict[str, dict[str, Any]]]:
        """State of the store at a point in time (time travel)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, branch, value FROM memory_versions v "
                "WHERE created_at <= ? AND version = ("
                "SELECT MAX(version) FROM memory_versions v2 "
                "WHERE v2.key = v.key AND v2.branch = v.branch AND v2.created_at <= ?)",
                (iso_ts, iso_ts),
            ).fetchall()
            out: dict[str, dict[str, dict[str, Any]]] = {}
            for key, branch, value in rows:
                out.setdefault(key, {})[branch] = json.loads(value)
            return out

    def all_keys(self, branch: str = "main") -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT key FROM memory_versions WHERE branch=?",
                (branch,),
            ).fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------- internals
    def _latest(self, key: str, branch: str) -> MemoryEntry | None:
        row = self._conn.execute(
            "SELECT key, branch, version, value, agent_id, reason, parent_version, "
            "value_hash, provenance, created_at FROM memory_versions "
            "WHERE key=? AND branch=? ORDER BY version DESC LIMIT 1",
            (key, branch),
        ).fetchone()
        return self._row_to_entry(row)

    def _insert(self, entry: MemoryEntry, provenance: str | None) -> None:
        self._conn.execute(
            "INSERT INTO memory_versions (key, branch, version, value, agent_id, reason, "
            "parent_version, value_hash, provenance, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                entry.key,
                entry.branch,
                entry.version,
                json.dumps(entry.value),
                entry.agent_id,
                entry.reason,
                entry.parent_version,
                entry.hash,
                provenance,
                entry.created_at,
            ),
        )
        self._conn.commit()

    def _row_to_entry(self, row: tuple | None) -> MemoryEntry | None:
        if row is None:
            return None
        return MemoryEntry(
            key=row[0],
            branch=row[1],
            version=row[2],
            value=json.loads(row[3]),
            agent_id=row[4],
            reason=row[5],
            parent_version=row[6],
            hash=row[7],
            created_at=datetime.fromisoformat(row[9]),
        )
