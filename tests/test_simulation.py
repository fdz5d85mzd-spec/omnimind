"""Simulation Sandbox tests: risk estimation, approval gating."""

import pytest

from omni.simulation.engine import SimulationSandbox


def test_low_risk_proceeds():
    s = SimulationSandbox()
    r = s.simulate("deploy:v0.2.0", "deploy", {"size": 0.5})
    assert r.needs_approval is False
    assert r.recommended_plan == "proceed"
    assert 0.5 <= r.confidence <= 1.0
    assert r.rollback_plan  # a rollback plan always exists


def test_payment_always_requires_approval():
    s = SimulationSandbox()
    r = s.simulate("charge:user-7", "payment")
    assert r.needs_approval is True
    assert "approval" in r.approval_reason.lower()
    approved = s.approve(r.simulation_id, "finance-ops")
    assert approved.needs_approval is False
    assert approved.approved_by == "finance-ops"


def test_file_delete_requires_approval():
    s = SimulationSandbox()
    r = s.simulate("rm -rf /var/data", "file_delete")
    assert r.needs_approval is True
    assert "approval" in r.approval_reason.lower()
    assert r.rollback_plan  # always proposes a rollback


def test_sensitive_db_migration_low_confidence():
    s = SimulationSandbox()
    r = s.simulate("migrate:users", "db_migration", {"sensitive_data": True, "size": 2.0})
    assert r.needs_approval is True
    assert r.risk_level.value in ("high", "critical")
    assert r.alternatives  # safer alternatives offered


def test_unknown_domain_rejected():
    s = SimulationSandbox()
    with pytest.raises(KeyError):
        s.simulate("x", "warp_drive")
