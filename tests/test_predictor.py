"""Failure predictor (M5b) tests: training, prediction, risk separation,
fallback backend, persistence, graceful degradation."""

import pytest

from omni.simulation.predictor import FailurePredictor, generate_synthetic_traces

RISKY_PLAN = {
    "name": "charge-users",
    "params": {"size": 2.5, "reversible": False, "sensitive_data": True},
    "steps": [
        {"name": f"step-{i}", "action": "payment.authorize_stub",
         "effects": ["payment.authorize_stub", "state.write", "logs.write"], "duration_ms": 800}
        for i in range(10)
    ],
}

TRIVIAL_PLAN = {
    "name": "dry-deploy",
    "params": {"size": 0.2, "reversible": True, "sensitive_data": False},
    "steps": [
        {"name": "validate", "action": "plan.validate", "effects": ["plan.validate"], "duration_ms": 5},
        {"name": "run", "action": "plan.run", "effects": ["plan.run"], "duration_ms": 10},
    ],
}


def test_backend_resolves_to_installed_engine():
    predictor = FailurePredictor()
    assert predictor.backend in ("sklearn", "fallback")
    assert predictor.model_info()["trained"] is False


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        FailurePredictor(backend="magic")


def test_train_and_predict_basics():
    predictor = FailurePredictor(seed=3)
    predictor.train(generate_synthetic_traces(n=150, seed=3))
    info = predictor.model_info()
    assert info["trained"] is True
    assert info["samples"] == 150

    out = predictor.predict_plan(TRIVIAL_PLAN, domain="deploy")
    assert 0.0 <= out["failure_probability"] <= 1.0
    assert out["risk_level"] in ("low", "medium", "high", "critical")
    assert isinstance(out["predicted_failures"], list)
    assert out["plan_name"] == "dry-deploy"


def test_learns_to_separate_high_from_low_risk():
    predictor = FailurePredictor(seed=5)
    predictor.train(generate_synthetic_traces(n=300, seed=5))
    risky = predictor.predict_plan(RISKY_PLAN, domain="payment")["failure_probability"]
    trivial = predictor.predict_plan(TRIVIAL_PLAN, domain="deploy")["failure_probability"]
    assert risky > trivial + 0.05
    assert predictor.predict_plan(RISKY_PLAN, domain="payment")["risk_level"] in (
        "high", "critical",
    ) or risky >= 0.5


def test_fallback_backend_degrades_gracefully():
    predictor = FailurePredictor(backend="fallback", seed=3)
    predictor.train(generate_synthetic_traces(n=150, seed=3))
    out = predictor.predict_plan(RISKY_PLAN, domain="payment")
    assert 0.0 <= out["failure_probability"] <= 1.0
    assert out["trained"] is True
    assert predictor.backend == "fallback"


def test_save_load_roundtrip_fallback(tmp_path):
    predictor = FailurePredictor(backend="fallback", seed=3)
    predictor.train(generate_synthetic_traces(n=80, seed=3))
    path = tmp_path / "predictor.json"
    predictor.save(str(path))

    loaded = FailurePredictor()
    loaded.load(str(path))
    assert loaded.backend == "fallback"
    assert loaded.model_info()["trained"] is True
    assert loaded.model_info()["samples"] == 80
    a = predictor.predict_plan(TRIVIAL_PLAN, domain="deploy")["failure_probability"]
    b = loaded.predict_plan(TRIVIAL_PLAN, domain="deploy")["failure_probability"]
    assert abs(a - b) < 1e-12


def test_predict_before_training_is_graceful():
    predictor = FailurePredictor()
    out = predictor.predict({"domain": "deploy", **{name: 0.0 for name in (
        "domain_idx", "size", "irreversible", "sensitive", "steps", "effects", "duration_s"
    )}})
    assert out["trained"] is False
    assert out["failure_probability"] == 0.0
    assert out["risk_level"] == "low"


def test_synthetic_generator_produces_both_labels():
    traces = generate_synthetic_traces(n=200, seed=42)
    assert len(traces) == 200
    labels = {t["label"] for t in traces}
    assert labels == {0, 1}
    assert "domain" in traces[0] and "duration_ms" in traces[0]
