"""Learning & Evaluation Pipeline: ingest evaluations, compute trends,
emit improvement reports. Drives routing, planning, and prompt improvements.

12 metric dimensions are collected per task (see MetricBundle). The pipeline:
* aggregates metrics by task type,
* fits a linear regression per metric to detect improving/regressing trends,
* generates an improvement report with the steepest regressions.

When constructed with `store=<EvaluationStore>` the pipeline persists every
evaluation (M6b) and reads history from the store, so trends survive restarts.
"""

from __future__ import annotations

import threading

from omni.contracts.evaluation import Evaluation
from omni.learning.aggregate import METRIC_FIELDS, aggregate, trends


class LearningPipeline:
    def __init__(self, window: int = 200, store=None) -> None:
        self._lock = threading.RLock()
        self._evaluations: list[Evaluation] = []
        self.window = window
        self._store = store

    def ingest(self, evaluation: Evaluation) -> Evaluation:
        with self._lock:
            self._evaluations.append(evaluation)
            if len(self._evaluations) > self.window:
                self._evaluations = self._evaluations[-self.window:]
        if self._store is not None:
            self._store.ingest(evaluation)
        return evaluation

    def evaluations(self) -> list[Evaluation]:
        if self._store is not None:
            return self._store.evaluations()
        with self._lock:
            return list(self._evaluations)

    def aggregate(self, task_type: str | None = None) -> dict[str, float]:
        return aggregate(self.evaluations(), task_type)

    def trends(self, task_type: str | None = None) -> dict[str, float]:
        return trends(self.evaluations(), task_type)

    def improvement_report(self) -> dict:
        all_trends = self.trends()
        regressing = sorted(
            ((m, s) for m, s in all_trends.items() if s < 0),
            key=lambda pair: pair[1],
        )
        improving = sorted(
            ((m, s) for m, s in all_trends.items() if s > 0),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return {
            "samples": len(self.evaluations()),
            "aggregate": self.aggregate(),
            "trends": all_trends,
            "top_regressions": [{"metric": m, "slope": s} for m, s in regressing[:3]],
            "top_improvements": [{"metric": m, "slope": s} for m, s in improving[:3]],
            "recommendations": _build_recommendations(regressing),
        }


def _build_recommendations(regressing: list[tuple[str, float]]) -> list[str]:
    by_metric = {m: s for m, s in regressing}
    out: list[str] = []
    for metric, advice in (
        ("execution_time_ms", "reroute high-latency tasks to faster model tier"),
        ("cost", "apply CostOptimizedScoring for this task class"),
        ("accuracy", "raise confidence threshold / add human review for low-accuracy tasks"),
        ("bug_density", "add static analysis + simulation gate before merge"),
        ("user_satisfaction", "collect structured feedback on failed tasks"),
    ):
        if metric in by_metric:
            out.append(f"{metric} trending down ({by_metric[metric]:+.3f}): {advice}")
    if not out:
        out.append("no regressing trends detected — maintain current routing")
    return out
