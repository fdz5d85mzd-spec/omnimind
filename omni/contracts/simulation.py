"""Simulation Sandbox contracts — simulate before you touch."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from omni.contracts.policy import RiskLevel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SimulationResult(BaseModel):
    simulation_id: str = Field(default_factory=lambda: f"sim_{uuid.uuid4().hex[:8]}")
    action: str
    domain: str
    risk_score: float  # 0..1
    risk_level: RiskLevel
    predicted_failures: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    rollback_plan: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    confidence: float  # 0..1
    recommended_plan: str = "proceed"
    needs_approval: bool = False
    approval_reason: str = ""
    approved_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    params: dict[str, Any] = Field(default_factory=dict)
