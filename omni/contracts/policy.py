"""Policy Engine contracts — RBAC/ABAC, limits, approvals, risk."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def risk_at_least(level: RiskLevel, threshold: RiskLevel) -> bool:
    return RISK_ORDER.index(level) >= RISK_ORDER.index(threshold)


class Principal(BaseModel):
    id: str
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Resource(BaseModel):
    type: str = "generic"
    attributes: dict[str, Any] = Field(default_factory=dict)

    def risk(self) -> RiskLevel:
        try:
            return RiskLevel(self.attributes.get("risk_level", "low"))
        except ValueError:
            return RiskLevel.LOW


class ABACCondition(BaseModel):
    """Condition over dotted paths: `resource.risk_level`, `principal.attributes.region`."""

    field: str
    op: str = "eq"  # eq ne gt gte lt lte in contains
    value: Any = None


class PolicyRule(BaseModel):
    """Ordered deny-before-allow rule. First match wins (highest priority)."""

    id: str
    action: str  # "agent.spawn" or "*"
    effect: str = "ALLOW"  # ALLOW | DENY
    roles: list[str] = Field(default_factory=list)  # empty = any role
    groups: list[str] = Field(default_factory=list)
    conditions: list[ABACCondition] = Field(default_factory=list)
    priority: int = 0
    risk_level: RiskLevel | None = None  # max risk allowed when ALLOW
    require_approval: bool = False
    approve_roles: list[str] = Field(default_factory=list)


class LimitPolicy(BaseModel):
    id: str
    scope: str = "GLOBAL"  # GLOBAL | GROUP | PRINCIPAL
    scope_value: str = "*"
    window_seconds: int = 3600
    max_cost: float | None = None
    max_calls: int | None = None


class LimitStatus(BaseModel):
    allowed: bool = True
    current_cost: float = 0.0
    current_calls: int = 0
    violations: list[str] = Field(default_factory=list)


class ApprovalStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PolicyDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"dec_{uuid.uuid4().hex[:8]}")
    allowed: bool
    matched_rule: str | None = None
    reason: str
    principal_id: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    require_approval: bool = False
    approve_roles: list[str] = Field(default_factory=list)
    approval_status: ApprovalStatus = ApprovalStatus.NONE
    limit_status: LimitStatus = Field(default_factory=LimitStatus)
    evaluated_at: datetime = Field(default_factory=_now)
