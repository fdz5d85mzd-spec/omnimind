"""Self Evolution Engine tests: propose → evaluate → adopt-if-gain gate."""

from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.evolution.engine import EvolutionEngine
from omni.learning.pipeline import LearningPipeline


def test_propose_evaluate_adopt():
    learn = LearningPipeline()
    learn.ingest(
        Evaluation(task_id="t0", agent_id="a", task_type="summary",
                   metrics=MetricBundle(accuracy=0.60, cost=0.2))
    )
    evo = EvolutionEngine(min_gain=0.05, learning=learn)
    p = evo.propose("routing", "cost routing", "route by cost", hypothesis="lower spend")
    assert p.status == "proposed"
    evo.evaluate(p.id, MetricBundle(accuracy=0.75, cost=0.1))
    p2 = evo.adopt_if_gain(p.id)
    assert p2.status == "adopted"
    assert p2.delta["accuracy"] >= 0.1
    assert p2.delta["cost"] <= -0.05


def test_reject_without_measurable_gain():
    learn = LearningPipeline()
    learn.ingest(
        Evaluation(task_id="t0", agent_id="a", task_type="summary",
                   metrics=MetricBundle(accuracy=0.9))
    )
    evo = EvolutionEngine(min_gain=0.05, learning=learn)
    p = evo.propose("agent", "rename", "no-op change", hypothesis="no effect")
    evo.evaluate(p.id, MetricBundle(accuracy=0.9, cost=0.0))
    p2 = evo.adopt_if_gain(p.id)
    assert p2.status == "rejected"


def test_unknown_domain_rejected():
    evo = EvolutionEngine()
    try:
        evo.propose("warp", "x", "y")
        raise AssertionError("should have raised")
    except KeyError:
        pass


def test_ledger_preserves_rejected_evidence():
    learn = LearningPipeline()
    learn.ingest(
        Evaluation(task_id="t0", agent_id="a", task_type="summary",
                   metrics=MetricBundle(accuracy=0.9))
    )
    evo = EvolutionEngine(min_gain=0.05, learning=learn)
    p = evo.propose("prompt", "reword", "no-op", hypothesis="no effect")
    evo.evaluate(p.id, MetricBundle(accuracy=0.9))
    evo.adopt_if_gain(p.id)
    ledger = evo.ledger()
    assert ledger[0].status == "rejected"  # negative evidence is kept
