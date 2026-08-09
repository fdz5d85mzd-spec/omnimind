"""Versioned Memory contracts — immutable, auditable knowledge."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryDiffOp(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"


class MemoryDiffItem(BaseModel):
    op: MemoryDiffOp
    path: str
    old_value: Any = None
    new_value: Any = None


class MemoryDiff(BaseModel):
    key: str
    branch: str
    from_version: int
    to_version: int
    items: list[MemoryDiffItem] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    key: str
    branch: str
    version: int
    value: dict[str, Any]
    agent_id: str
    reason: str
    parent_version: int | None = None
    hash: str = ""
    created_at: datetime = Field(default_factory=_now)


class RollbackRequest(BaseModel):
    key: str
    branch: str = "main"
    target_version: int
    agent_id: str
    reason: str


class BranchRequest(BaseModel):
    key: str
    source_branch: str = "main"
    source_version: int | None = None  # None = latest
    new_branch: str
    agent_id: str
    reason: str
