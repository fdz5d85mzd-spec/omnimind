"""Persistent evaluation store (M6b) tests — history survives restarts."""

from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.learning.pipeline import LearningPipeline
from omni.learning.store import EvaluationStore


def _eval(task: str, accuracy: float, cost: float = 0.1) -> Evaluation:
    return Evaluation(
        task_id=task, agent_id="a1", task_type="summary",
        metrics=MetricBundle(accuracy=accuracy, cost=cost, execution_time_ms=100),
    )


def test_store_roundtrip_and_persistence():
    store = EvaluationStore()
    store.ingest(_eval("t1", 0.8))
    store.ingest(_eval("t2", 0.9))
    assert store.count() == 2
    agg = store.aggregate()
    assert agg["accuracy"] == 0.85
    assert len(store.evaluations()) == 2


def test_pipeline_uses_store_for_history_and_trends():
    store = EvaluationStore()
    pipeline = LearningPipeline(store=store)
    for acc in [0.5, 0.6, 0.7, 0.8]:
        pipeline.ingest(_eval(f"t{acc}", acc))
    assert store.count() == 4
    assert pipeline.trends()["accuracy"] > 0.05
    assert pipeline.improvement_report()["samples"] == 4


def test_ingest_duplicate_id_is_replace():
    store = EvaluationStore()
    store.ingest(_eval("t1", 0.8))
    dup = _eval("t1", 0.95)
    dup.id = store.evaluations()[0].id  # same evaluation id
    store.ingest(dup)
    assert store.count() == 1  # replaced, not duplicated
