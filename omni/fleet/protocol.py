"""Fleet protocol contracts (M8)."""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _now().isoformat()


def _fqdn() -> str:
    try:
        return socket.getfqdn()
    except Exception:  # pragma: no cover - node without DNS
        return os.uname().nodename if hasattr(os, "uname") else "localhost"


def node_id() -> str:
    return f"node_{uuid.uuid4().hex[:12]}"


class NodeRole(str, Enum):
    LEADER = "leader"
    FOLLOWER = "follower"


class NodeHealth(BaseModel):
    node: str
    role: NodeRole = NodeRole.FOLLOWER
    agents_running: int = 0
    agents_total: int = 0
    avg_load: float = 0.0
    queue_depth: int = 0
    cost_spent: float = 0.0
    ewma_rate: float = 0.0
    uptime_s: float = 0.0
    ts: str = Field(default_factory=_utcnow_iso)


class WorkloadStats(BaseModel):
    total_agents: int = 0
    total_queued: int = 0
    average_load: float = 0.0
    total_cost: float = 0.0
    arrival_rate: float = 0.0


class LeaderInfo(BaseModel):
    leader: str
    term: int = 0
    elected_at: str = Field(default_factory=_utcnow_iso)


class NodeAnnouncement(BaseModel):
    """Payload a node posts at registry nodes keyed by node id."""

    announcement_id: str = Field(default_factory=lambda: f"ann_{uuid.uuid4().hex[:8]}")
    node: str = Field(default_factory=node_id)
    fqdn: str = Field(default_factory=_fqdn)
    version: str = "0.4.0"
    capacity: int = 32  # max agents this node can host
    role: NodeRole = NodeRole.FOLLOWER
    health: NodeHealth | None = None
    ts: str = Field(default_factory=_utcnow_iso)

    @property
    def key(self) -> str:
        return f"fleet.node.{self.node}"
