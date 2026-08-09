"""Learning & Evaluation contracts — every task becomes training data."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MetricBundle(BaseModel):
    """The 12 evaluation dimensions collected for every completed task."""

    execution_time_ms: float = 0.0
    cost: float = 0.0
    accuracy: float = 0.0
    reasoning_quality: float = 0.0
    user_satisfaction: float = 0.0
    code_quality: float = 0.0
    architecture_quality: float = 0.0
    documentation_quality: float = 0.0
    bug_density: float = 0.0
    security_score: float = 0.0
    performance: float = 0.0
    maintainability: float = 0.0


class Evaluation(BaseModel):
    id: str = Field(default_factory=lambda: f"eval_{uuid.uuid4().hex[:8]}")
    task_id: str
    agent_id: str
    task_type: str = "generic"
    metrics: MetricBundle = Field(default_factory=MetricBundle)
    summary: str = ""
    completed_at: datetime = Field(default_factory=_now)
