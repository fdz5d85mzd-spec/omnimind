"""ReplayLedger + policy integration tests (M6b audit & replay)."""

from omni.audit.ledger import ReplayLedger
from omni.contracts.policy import Principal, Resource
from omni.policy.engine import PolicyEngine, make_seed_rules


def test_ledger_append_only_and_filter():
    ledger = ReplayLedger()
    ledger.record("policy", "ops-1", "decision.allow", {"allowed": True}, decision_id="dec_1")
    ledger.record("policy", "ops-1", "decision.deny", {"allowed": False}, decision_id="dec_2")
    ledger.record("twin", "twin", "snapshot", {"tick": 1})
    assert ledger.count() == 3

    by_subject = ledger.replay(subject="ops-1")
    assert [e["decision_id"] for e in by_subject] == ["dec_1", "dec_2"]

    by_subsystem = ledger.replay(subsystem="twin")
    assert len(by_subsystem) == 1
    assert by_subsystem[0]["event"] == "snapshot"


def test_policy_engine_records_every_decision():
    ledger = ReplayLedger()
    policy = PolicyEngine(make_seed_rules(), ledger=ledger)
    decision = policy.evaluate(
        Principal(id="ops-1", roles=["operator"]),
        "agent.spawn",
        Resource(type="agent", attributes={"risk_level": "low"}),
    )
    assert ledger.count() == 1
    events = ledger.replay(subject="ops-1")
    assert events[0]["decision_id"] == decision.decision_id
    assert events[0]["payload"]["allowed"] is True
    assert events[0]["payload"]["matched_rule"] == "rule_allow_agent_ops"


def test_ledger_is_immutable_replay_order_stable():
    ledger = ReplayLedger()
    ledger.record("a", "s", "e1", {})
    ledger.record("b", "s", "e2", {})
    replay = ledger.replay(limit=100)
    assert [e["event"] for e in replay] == ["e1", "e2"]
