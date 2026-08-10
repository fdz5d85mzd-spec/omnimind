"""ADMIN_API_KEY guard on the mutating admin endpoints (policy approve,
lockdown) — unset means open (unchanged legacy behavior), set means the
caller must present a matching X-Admin-Key header."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)


def _pending_decision_id() -> str:
    r = client.post(
        "/policy/evaluate",
        json={
            "principal": {"id": "rel1", "roles": ["release-engineer"]},
            "action": "deploy",
            "resource": {"attributes": {"risk_level": "low"}},
        },
    )
    assert r.status_code == 200
    return r.json()["decision_id"]


def test_lockdown_open_when_admin_api_key_unset():
    with patch.dict("os.environ", {}, clear=True):
        r = client.post("/policy/lockdown", params={"enabled": True})
    assert r.status_code == 200
    client.post("/policy/lockdown", params={"enabled": False})  # reset for other tests


def test_lockdown_rejects_missing_or_wrong_key_when_configured():
    with patch.dict("os.environ", {"ADMIN_API_KEY": "s3cret"}, clear=True):
        r = client.post("/policy/lockdown", params={"enabled": True})
        assert r.status_code == 403

        r = client.post(
            "/policy/lockdown",
            params={"enabled": True},
            headers={"X-Admin-Key": "wrong"},
        )
        assert r.status_code == 403


def test_lockdown_accepts_correct_key_when_configured():
    with patch.dict("os.environ", {"ADMIN_API_KEY": "s3cret"}, clear=True):
        r = client.post(
            "/policy/lockdown",
            params={"enabled": True},
            headers={"X-Admin-Key": "s3cret"},
        )
        assert r.status_code == 200
        assert r.json() == {"lockdown": True}
        client.post(
            "/policy/lockdown",
            params={"enabled": False},
            headers={"X-Admin-Key": "s3cret"},
        )


def test_policy_approve_rejects_wrong_key_when_configured():
    decision_id = _pending_decision_id()
    with patch.dict("os.environ", {"ADMIN_API_KEY": "s3cret"}, clear=True):
        r = client.post(
            f"/policy/approve/{decision_id}",
            params={"approver_role": "release-manager"},
            headers={"X-Admin-Key": "nope"},
        )
        assert r.status_code == 403


def test_policy_approve_accepts_correct_key_when_configured():
    decision_id = _pending_decision_id()
    with patch.dict("os.environ", {"ADMIN_API_KEY": "s3cret"}, clear=True):
        r = client.post(
            f"/policy/approve/{decision_id}",
            params={"approver_role": "release-manager"},
            headers={"X-Admin-Key": "s3cret"},
        )
        assert r.status_code == 200
        assert r.json()["allowed"] is True


def test_policy_approve_open_when_admin_api_key_unset():
    decision_id = _pending_decision_id()
    with patch.dict("os.environ", {}, clear=True):
        r = client.post(
            f"/policy/approve/{decision_id}",
            params={"approver_role": "release-manager"},
        )
    assert r.status_code == 200
