"""Skill Marketplace contracts — every capability is a versioned skill."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SkillKind(str, Enum):
    OFFICIAL = "official"
    COMMUNITY = "community"
    PRIVATE = "private"
    ENTERPRISE = "enterprise"
    LOCAL = "local"
    REMOTE = "remote"


class SkillInterface(BaseModel):
    """Everything a skill exposes."""

    actions: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    documentation: str = ""
    tests: list[str] = Field(default_factory=list)
    ui_components: list[str] = Field(default_factory=list)
    api: list[str] = Field(default_factory=list)


class SkillVersion(BaseModel):
    version: str
    changelog: str = ""
    interface: SkillInterface = Field(default_factory=SkillInterface)
    published_at: datetime = Field(default_factory=_now)


class SkillManifest(BaseModel):
    """The installable, versioned unit of capability."""

    id: str = Field(default_factory=lambda: f"skill_{uuid.uuid4().hex[:8]}")
    name: str
    description: str = ""
    kind: SkillKind = SkillKind.COMMUNITY
    author: str = "system"
    tags: list[str] = Field(default_factory=list)
    versions: list[SkillVersion] = Field(default_factory=list)
    latest_version: str = "0.0.0"
    installs: int = 0
    rating_sum: int = 0
    rating_count: int = 0
    source_url: str = ""  # provenance for remotely-synced skills
    last_synced: datetime | None = None
    created_at: datetime = Field(default_factory=_now)

    @property
    def rating(self) -> float:
        if self.rating_count == 0:
            return 0.0
        return round(self.rating_sum / self.rating_count, 2)

    def latest(self) -> SkillVersion | None:
        if not self.versions:
            return None
        return self.versions[-1]
