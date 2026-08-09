"""Learning & Evaluation Pipeline tests: ingestion, aggregation, trends,
improvement reports."""

from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.learning.pipeline import LearningPipeline


def _eval(task: str, agent: str, accuracy: float, cost: float, time_ms: float) -> Evaluation:
    return Evaluation(
        task_id=task, agent_id=agent, task_type="summary",
        metrics=MetricBundle(accuracy=accuracy, cost=cost, execution_time_ms=time_ms),
    )


def test_ingest_and_aggregate():
    p = LearningPipeline()
    p.ingest(_eval("t1", "a1", 0.8, 0.1, 100))
    p.ingest(_eval("t2", "a1", 0.9, 0.2, 200))
    agg = p.aggregate()
    assert agg["accuracy"] == 0.85
    assert agg["cost"] == 0.15
    assert agg["execution_time_ms"] == 150.0
    assert p.aggregate("summary")["accuracy"] == 0.85


def test_trends_and_improvement_report():
    p = LearningPipeline()
    for i, acc in enumerate([0.5, 0.6, 0.7, 0.8]):
        p.ingest(_eval(f"t{i}", "a1", acc, 0.1, 100))
    trends = p.trends()
    assert trends["accuracy"] > 0.05  # improving slope
    report = p.improvement_report()
    assert report["samples"] == 4
    assert report["top_improvements"][0]["metric"] == "accuracy"
    assert isinstance(report["recommendations"], list)


def test_regression_detected():
    p = LearningPipeline()
    for i, acc in enumerate([0.9, 0.8, 0.7, 0.6]):
        p.ingest(_eval(f"t{i}", "a1", acc, 0.1, 100))
    trends = p.trends()
    assert trends["accuracy"] < -0.05
    assert p.improvement_report()["top_regressions"][0]["metric"] == "accuracy"
