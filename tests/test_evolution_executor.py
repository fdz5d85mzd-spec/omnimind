"""M7b mutation executor tests: adopt-gate, config mutation, revert,
versioned prompt mutation, policy gate."""

import pytest

from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.evolution.engine import EvolutionEngine
from omni.evolution.executor import EvolutionExecutor
from omni.learning.pipeline import LearningPipeline
from omni.memory.store import MemoryStore
from omni.policy.engine import PolicyEngine, make_seed_rules


def _adopted_proposal(evo: EvolutionEngine):
    p = evo.propose("routing", "cost routing", "route by cost", hypothesis="lower spend")
    evo.evaluate(p.id, MetricBundle(accuracy=0.9, cost=0.05))
    evo.adopt_if_gain(p.id)
    return p


def _evo_with_gain_history() -> EvolutionEngine:
    learn = LearningPipeline()
    learn.ingest(
        Evaluation(task_id="seed", agent_id="a", task_type="summary",
                   metrics=MetricBundle(accuracy=0.6, cost=0.2))
    )
    return EvolutionEngine(learning=learn)


def test_config_mutation_requires_adopted_proposal():
    evo = _evo_with_gain_history()
    executor = EvolutionExecutor(evolution=evo, config={"routing": {"mode": "balanced"}})
    pending = evo.propose("routing", "bg job", "no-op", hypothesis="no effect")
    with pytest.raises(ValueError):
        executor.apply(pending.id, "routing", {"set": {"routing.mode": "cost"}})


def test_routing_mutation_applies_and_reverts():
    evo = _evo_with_gain_history()
    config = {"routing": {"mode": "balanced"}}
    executor = EvolutionExecutor(evolution=evo, config=config)
    proposal = _adopted_proposal(evo)

    record = executor.apply(proposal.id, "routing", {"set": {"routing.mode": "cost", "routing.hot": True}})
    assert record.status == "applied"
    assert config["routing"]["mode"] == "cost"
    assert record.before["values"]["routing.mode"] == "balanced"
    assert record.after["values"]["routing.mode"] == "cost"

    executor.revert(record.mutation_id)
    assert config["routing"]["mode"] == "balanced"
    assert record.status == "reverted"
    assert record.reverted_at is not None


def test_prompt_mutation_is_versioned_in_memory():
    evo = _evo_with_gain_history()
    store = MemoryStore()
    executor = EvolutionExecutor(evolution=evo, memory=store)
    proposal = _adopted_proposal(evo)

    record = executor.apply(proposal.id, "prompt", {"prompt": {"task_type": "summary", "template": "be concise"}})
    entry = store.read("prompts.summary")
    assert entry is not None
    assert entry.value["template"] == "be concise"
    assert entry.agent_id == "evolution"

    executor.revert(record.mutation_id)
    reverted = store.read("prompts.summary")
    assert reverted.value["template"] is None  # before-state restored as a new version
    assert reverted.version > entry.version  # immutability preserved


def test_policy_engine_gates_mutation():
    evo = _evo_with_gain_history()
    proposal = _adopted_proposal(evo)
    strict = PolicyEngine()  # no rules → default deny
    executor = EvolutionExecutor(evolution=evo, policy=strict)
    with pytest.raises(PermissionError):
        executor.apply(proposal.id, "routing", {"set": {"routing.mode": "cost"}})


def test_standard_policy_allows_system_admin_mutation():
    evo = _evo_with_gain_history()
    proposal = _adopted_proposal(evo)
    executor = EvolutionExecutor(evolution=evo, policy=PolicyEngine(make_seed_rules()),
                                 config={"routing": {"mode": "balanced"}})
    record = executor.apply(proposal.id, "routing", {"set": {"routing.mode": "quality"}})
    assert record.status == "applied"


def test_ledger_records_mutations():
    from omni.audit.ledger import ReplayLedger

    evo = _evo_with_gain_history()
    proposal = _adopted_proposal(evo)
    ledger = ReplayLedger()
    executor = EvolutionExecutor(evolution=evo, ledger=ledger, config={})
    executor.apply(proposal.id, "orchestrator_config", {"set": {"max_agents": 99}})
    events = ledger.replay(subsystem="evolution")
    assert len(events) == 1
    assert events[0]["event"] == "mutation.applied"
