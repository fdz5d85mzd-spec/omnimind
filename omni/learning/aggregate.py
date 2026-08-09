"""Shared aggregation helpers for the evaluation pipeline and the
persistent metrics store (M6b)."""

from __future__ import annotations

import statistics
from typing import Iterable

from omni.contracts.evaluation import Evaluation, MetricBundle

METRIC_FIELDS = list(MetricBundle.model_fields)


def aggregate(evaluations: Iterable[Evaluation], task_type: str | None = None) -> dict[str, float]:
    series = [e for e in evaluations if task_type is None or e.task_type == task_type]
    if not series:
        return {}
    return {
        field: round(statistics.fmean(getattr(e.metrics, field) for e in series), 4)
        for field in METRIC_FIELDS
    }


def _trend(values: list[float]) -> float:
    """Normalized slope: +1 improving, -1 regressing, 0 flat."""
    n = len(values)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / denom
    return round(max(-1.0, min(1.0, slope / max(my, 0.01))), 4)


def trends(evaluations: Iterable[Evaluation], task_type: str | None = None) -> dict[str, float]:
    series = [e for e in evaluations if task_type is None or e.task_type == task_type]
    out: dict[str, float] = {}
    for field in METRIC_FIELDS:
        out[field] = _trend([getattr(e.metrics, field) for e in series])
    return out
