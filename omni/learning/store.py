"""Persistent evaluation store (M6b): evaluations survive process restarts.

Backed by SQLite. The Learning Pipeline uses this when constructed with
`store=<EvaluationStore>`; all aggregation and trend logic is shared with the
pipeline via `omni.learning.aggregate`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.learning.aggregate import aggregate, trends

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    metrics TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_type ON evaluations (task_type);
CREATE INDEX IF NOT EXISTS idx_eval_task ON evaluations (task_id);
"""


class EvaluationStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def ingest(self, evaluation: Evaluation) -> Evaluation:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO evaluations (id, task_id, agent_id, task_type, metrics, summary, completed_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    evaluation.id,
                    evaluation.task_id,
                    evaluation.agent_id,
                    evaluation.task_type,
                    json.dumps(evaluation.metrics.model_dump()),
                    evaluation.summary,
                    evaluation.completed_at.isoformat(),
                ),
            )
            self._conn.commit()
            return evaluation

    def evaluations(self) -> list[Evaluation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, task_id, agent_id, task_type, metrics, summary, completed_at "
                "FROM evaluations ORDER BY completed_at ASC"
            ).fetchall()
            out = []
            for row in rows:
                out.append(
                    Evaluation(
                        id=row[0],
                        task_id=row[1],
                        agent_id=row[2],
                        task_type=row[3],
                        metrics=MetricBundle(**json.loads(row[4])),
                        summary=row[5],
                    )
                )
            return out

    def aggregate(self, task_type: str | None = None) -> dict[str, float]:
        return aggregate(self.evaluations(), task_type)

    def trends(self, task_type: str | None = None) -> dict[str, float]:
        return trends(self.evaluations(), task_type)

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
