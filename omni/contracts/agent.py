"""Agent and task contracts shared across the platform."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class AgentType(str, Enum):
    SYSTEM = "system"
    ORCHESTRATOR = "orchestrator"
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    SPECIALIST = "specialist"


class AgentStatus(str, Enum):
    RUNNING = "running"
    IDLE = "idle"
    WAITING = "waiting"
    BLOCKED = "blocked"
    TERMINATED = "terminated"


class AgentSpec(BaseModel):
    """A live agent under the Meta-Orchestrator's supervision."""

    id: str = Field(default_factory=lambda: _id("agt"))
    name: str
    agent_type: AgentType = AgentType.WORKER
    status: AgentStatus = AgentStatus.IDLE
    skills: list[str] = Field(default_factory=list)
    load: float = Field(default=0.0, ge=0.0, le=1.0)
    queue_depth: int = Field(default=0, ge=0)
    model: str | None = None
    cost_spent: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=_now)
    last_heartbeat: datetime = Field(default_factory=_now)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskSpec(BaseModel):
    """A unit of work flowing through the pool."""

    id: str = Field(default_factory=lambda: _id("task"))
    name: str
    signature: str = ""  # content hash used for duplicate detection
    required_skills: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=10)
    risk_level: str = "low"
    payload: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.QUEUED
    assignee: str | None = None
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
