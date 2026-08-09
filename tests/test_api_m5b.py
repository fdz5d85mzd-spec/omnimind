"""M5b control-plane API tests: dryrun, predict, predictor status/train."""

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)

PLAN = {
    "name": "deploy",
    "params": {"size": 0.5},
    "steps": [
        {"name": "validate", "action": "plan.validate", "effects": ["plan.validate"], "duration_ms": 5},
        {"name": "apply", "action": "migration.apply", "effects": ["migration.apply", "config.write"], "duration_ms": 10},
    ],
}


def test_dryrun_endpoint():
    r = client.post("/simulation/dryrun", json={"plan": PLAN, "domain": "db_migration"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "clean"
    assert body["steps_total"] == 2
    assert body["effects_total"] == 3


def test_dryrun_rejects_bad_plan():
    r = client.post("/simulation/dryrun", json={"plan": {"name": "x"}, "domain": "generic"})
    assert r.status_code == 400


def test_predictor_status_is_trained_by_default():
    r = client.get("/simulation/predictor/status")
    assert r.status_code == 200
    info = r.json()
    assert info["trained"] is True
    assert info["backend"] in ("sklearn", "fallback")
    assert info["samples"] == 120


def test_predict_endpoint():
    r = client.post("/simulation/predict", json={"plan": PLAN, "domain": "db_migration"})
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["failure_probability"] <= 1.0
    assert body["risk_level"] in ("low", "medium", "high", "critical")


def test_predictor_train_endpoint_retrains():
    traces = [
        {
            "domain": "deploy",
            "size": 0.5,
            "reversible": True,
            "sensitive_data": False,
            "num_steps": 3,
            "num_effects": 3,
            "duration_ms": 100,
            "label": 0,
        }
    ]
    r = client.post("/simulation/predictor/train", json={"traces": traces})
    assert r.status_code == 200
    info = r.json()
    assert info["trained"] is True
    assert info["samples"] == 1
