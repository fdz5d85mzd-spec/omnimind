"""Policy Engine tests: RBAC, ABAC, default deny, deny-before-allow,
risk gating, approval chains, limits, emergency lockdown."""

from omni.contracts.policy import ApprovalStatus, LimitPolicy, Principal, Resource
from omni.policy.engine import PolicyEngine, make_seed_rules


def engine() -> PolicyEngine:
    return PolicyEngine(make_seed_rules())


def test_default_deny():
    e = engine()
    d = e.evaluate(Principal(id="u1", roles=["reader"]), "system.shutdown")
    assert d.allowed is False
    assert "no policy matches" in d.reason


def test_rbac_allow():
    e = engine()
    d = e.evaluate(
        Principal(id="u1", roles=["operator"]),
        "agent.spawn",
        Resource(type="agent", attributes={"risk_level": "low"}),
    )
    assert d.allowed is True
    assert d.matched_rule == "rule_allow_agent_ops"


def test_abac_risk_gate():
    e = engine()
    d = e.evaluate(
        Principal(id="u1", roles=["operator"]),
        "agent.spawn",
        Resource(type="agent", attributes={"risk_level": "critical"}),
    )
    assert d.allowed is False


def test_deny_beats_allow():
    e = engine()
    d = e.evaluate(
        Principal(id="u1", roles=["guest"]),
        "agent.spawn",
        Resource(type="agent", attributes={"risk_level": "low"}),
    )
    assert d.allowed is False
    assert d.matched_rule == "rule_deny_guests_write"


def test_role_scoped_write():
    e = engine()
    assert e.evaluate(Principal(id="u1", roles=["reader"]), "memory.write").allowed is False
    assert e.evaluate(Principal(id="u1", roles=["operator"]), "memory.write").allowed is True


def test_approval_chain_roundtrip():
    e = engine()
    d = e.evaluate(
        Principal(id="rel1", roles=["release-engineer"]),
        "deploy",
        Resource(type="service", attributes={"risk_level": "low"}),
    )
    assert d.allowed is False
    assert d.require_approval is True
    assert d.approval_status == ApprovalStatus.PENDING
    assert len(e.pending_approvals()) == 1

    approved = e.approve(d.decision_id, "release-manager")
    assert approved.allowed is True
    assert approved.approval_status == ApprovalStatus.APPROVED
    assert len(e.pending_approvals()) == 0


def test_approval_wrong_role_rejected():
    e = engine()
    d = e.evaluate(
        Principal(id="rel1", roles=["release-engineer"]),
        "deploy",
        Resource(attributes={"risk_level": "low"}),
    )
    rejected = e.approve(d.decision_id, "reader")
    assert rejected.allowed is False
    assert rejected.approval_status == ApprovalStatus.REJECTED


def test_cost_limit_rolling_window():
    e = engine()
    e.add_limit(LimitPolicy(id="cap", scope="GLOBAL", scope_value="*", window_seconds=3600, max_cost=10.0))
    first = e.evaluate(Principal(id="u1", roles=["operator"]), "memory.write", cost=8.0)
    assert first.allowed is True
    second = e.evaluate(Principal(id="u1", roles=["operator"]), "memory.write", cost=8.0)
    assert second.allowed is False
    assert any("cost limit" in v for v in second.limit_status.violations)


def test_call_limit_per_principal():
    e = engine()
    e.add_limit(LimitPolicy(id="calls", scope="PRINCIPAL", scope_value="*", window_seconds=60, max_calls=2))
    p = Principal(id="u1", roles=["operator"])
    assert e.evaluate(p, "memory.read", calls=1).allowed is True
    assert e.evaluate(p, "memory.read", calls=1).allowed is True
    assert e.evaluate(p, "memory.read", calls=1).allowed is False


def test_emergency_lockdown():
    e = engine()
    e.set_lockdown(True)
    assert e.evaluate(Principal(id="u1", roles=["operator"]), "memory.read").allowed is False
    assert e.evaluate(Principal(id="root", roles=["system.admin"]), "memory.read").allowed is True


def test_decision_log_grows():
    e = engine()
    e.evaluate(Principal(id="u1", roles=["reader"]), "memory.read")
    e.evaluate(Principal(id="u1", roles=["guest"]), "agent.spawn")
    log = e.decision_log()
    assert len(log) >= 2
    assert all("reason" in d.model_dump() for d in log)
