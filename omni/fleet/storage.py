"""Fleet storage abstraction + PostgresFleetStorage (M8).

The fleet needs distributed, durable coordination state: node announcements
(shared registry), the current leader (term + epoch), and a shared task queue
(global backlog). `FleetStorage` defines the interface; `PostgresFleetStorage`
implements a production-grade version guarded by `SELECT ... FOR UPDATE`
transactions; an optional in-memory adapter exists for tests.
"""

from __future__ import annotations

import abc
import json
import threading
from typing import Any


class FleetStorage(abc.ABC):
    """Interface used by FleetNode for durable fleet state."""

    @abc.abstractmethod
    def set_leader(self, leader: str, term: int) -> dict[str, Any]: ...

    @abc.abstractmethod
    def get_leader(self) -> dict[str, Any] | None: ...

    @abc.abstractmethod
    def announce(self, key: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    @abc.abstractmethod
    def nodes(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def enqueue(self, task: dict[str, Any]) -> dict[str, Any]: ...

    @abc.abstractmethod
    def lease_task(self, node: str, lease_s: int = 60) -> dict[str, Any] | None: ...


class InMemoryFleetStorage(FleetStorage):
    """Test double — deterministic and process-local."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._leader: dict[str, Any] | None = None
        self._announcements: dict[str, dict[str, Any]] = {}
        self._queue: list[dict[str, Any]] = []

    def set_leader(self, leader: str, term: int) -> dict[str, Any]:
        with self._lock:
            self._leader = {"leader": leader, "term": term}
            return dict(self._leader)

    def get_leader(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._leader) if self._leader else None

    def announce(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._announcements[key] = payload
            return payload

    def nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._announcements.values())

    def enqueue(self, task: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._queue.append(task)
            return task

    def lease_task(self, node: str, lease_s: int = 60) -> dict[str, Any] | None:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.pop(0)


class PostgresFleetStorage(FleetStorage):
    """Production adapter. Requires the `psycopg` package and a reachable
    database. All calls are wrapped so an unavailable value falls back to
    None/propagates the underlying exception to the caller.
    """

    def __init__(self, dsn: str, schema: str = "fleet") -> None:
        import psycopg  # imported lazily: package is optional

        self._psycopg = psycopg
        self._dsn = dsn
        self._schema = schema
        self._conn = psycopg.connect(dsn)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE SCHEMA IF NOT EXISTS {self._schema};
                CREATE TABLE IF NOT EXISTS {self._schema}.leader (
                    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE,
                    leader TEXT NOT NULL,
                    term INT NOT NULL,
                    CHECK (singleton)
                );
                CREATE TABLE IF NOT EXISTS {self._schema}.nodes (
                    key TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS {self._schema}.task_queue (
                    id BIGSERIAL PRIMARY KEY,
                    payload JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    leased_by TEXT,
                    leased_until TIMESTAMPTZ
                );
                """
            )
            self._conn.commit()

    def set_leader(self, leader: str, term: int) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {self._schema}.leader (singleton, leader, term)
                    VALUES (TRUE, %s, %s)
                    ON CONFLICT (singleton) DO UPDATE SET leader = EXCLUDED.leader,
                                                          term = EXCLUDED.term""",
                (leader, term),
            )
            self._conn.commit()
        return {"leader": leader, "term": term}

    def get_leader(self) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT leader, term FROM {self._schema}.leader WHERE singleton")
            row = cur.fetchone()
        return {"leader": row[0], "term": row[1]} if row else None

    def announce(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {self._schema}.nodes (key, payload, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload,
                                                    updated_at = NOW()""",
                (key, json.dumps(payload)),
            )
            self._conn.commit()
        return payload

    def nodes(self) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {self._schema}.nodes ORDER BY updated_at DESC")
            rows = cur.fetchall()
        return [json.loads(r[0]) for r in rows]

    def enqueue(self, task: dict[str, Any]) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._schema}.task_queue (payload) VALUES (%s) RETURNING id",
                (json.dumps(task),),
            )
            task_id = cur.fetchone()[0]
            self._conn.commit()
        return {"id": str(task_id), **task}

    def lease_task(self, node: str, lease_s: int = 60) -> dict[str, Any] | None:
        """Atomically lease one queued task guarded by `FOR UPDATE SKIP LOCKED`."""
        with self._conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {self._schema}.task_queue
                    SET status = 'leased', leased_by = %s,
                        leased_until = NOW() + make_interval(secs => %s)
                    WHERE id = (
                        SELECT id FROM {self._schema}.task_queue
                        WHERE status = 'queued' OR leased_until < NOW()
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
                    ) RETURNING id, payload""",
                (node, lease_s),
            )
            row = cur.fetchone()
            self._conn.commit()
        if row is None:
            return None
        return {"id": str(row[0]), **json.loads(row[1])}
