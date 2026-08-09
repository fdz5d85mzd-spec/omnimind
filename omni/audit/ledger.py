"""ReplayLedger — append-only, queryable record of every system decision.

Used by the Policy Engine (every decision is recorded), the Digital Twin
(replay endpoint), and any subsystem that must answer "why did this happen?".

Design
------
* Rows are immutable; nothing is ever updated or deleted.
* Filter by `subject` (a principal id, a task id, a node id...) or by
  `subsystem`; order is insertion order (stable replay).
* `replay()` returns fully decoded events so the whole reasoning chain of a
  decision can be re-examined.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    subsystem TEXT NOT NULL,
    subject TEXT NOT NULL,
    event TEXT NOT NULL,
    decision_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_subject ON ledger (subject);
CREATE INDEX IF NOT EXISTS idx_ledger_subsystem ON ledger (subsystem, ts);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplayLedger:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record(
        self,
        subsystem: str,
        subject: str,
        event: str,
        payload: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one immutable event. Returns the stored event."""
        with self._lock:
            row = {
                "id": None,
                "ts": _now(),
                "subsystem": subsystem,
                "subject": subject,
                "event": event,
                "decision_id": decision_id,
                "payload": payload or {},
            }
            cur = self._conn.execute(
                "INSERT INTO ledger (ts, subsystem, subject, event, decision_id, payload) VALUES (?,?,?,?,?,?)",
                (row["ts"], row["subsystem"], row["subject"], row["event"], row["decision_id"], json.dumps(row["payload"])),
            )
            self._conn.commit()
            row["id"] = cur.lastrowid
            return row

    def replay(self, subject: str | None = None, subsystem: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Replay events, filtered and ordered exactly as recorded."""
        with self._lock:
            sql = "SELECT id, ts, subsystem, subject, event, decision_id, payload FROM ledger WHERE 1=1"
            args: list[Any] = []
            if subject is not None:
                sql += " AND subject = ?"
                args.append(subject)
            if subsystem is not None:
                sql += " AND subsystem = ?"
                args.append(subsystem)
            sql += " ORDER BY id ASC LIMIT ?"
            args.append(limit)
            rows = self._conn.execute(sql, args).fetchall()
            return [self._row(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    def _row(self, r: tuple) -> dict[str, Any]:
        return {
            "id": r[0],
            "ts": r[1],
            "subsystem": r[2],
            "subject": r[3],
            "event": r[4],
            "decision_id": r[5],
            "payload": json.loads(r[6]),
        }
