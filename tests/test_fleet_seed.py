"""The standing 40-agent fleet roster: registered for real through the
orchestrator, exactly one leader, idempotent across repeated seeding."""

from omni.agents.fleet_seed import LEADER_NAME, SEED_ROSTER, leader_id, seed_fleet
from omni.contracts.agent import AgentType
from omni.orchestrator.engine import MetaOrchestrator


def test_roster_has_forty_agents_and_one_leader():
    assert len(SEED_ROSTER) == 40
    leaders = [name for name, t, _ in SEED_ROSTER if t == AgentType.SUPERVISOR]
    assert leaders == [LEADER_NAME]


def test_roster_names_are_unique():
    names = [name for name, _, _ in SEED_ROSTER]
    assert len(names) == len(set(names))


def test_seed_fleet_registers_all_forty():
    orch = MetaOrchestrator()
    created = seed_fleet(orch)
    assert len(created) == 40
    assert len(orch.agents()) == 40


def test_seed_fleet_is_idempotent():
    orch = MetaOrchestrator()
    seed_fleet(orch)
    second_pass = seed_fleet(orch)
    assert second_pass == []
    assert len(orch.agents()) == 40


def test_leader_id_resolves_the_supervisor():
    orch = MetaOrchestrator()
    seed_fleet(orch)
    agent_id = leader_id(orch)
    assert agent_id is not None
    leader = next(a for a in orch.agents() if a.id == agent_id)
    assert leader.name == LEADER_NAME
    assert leader.agent_type == AgentType.SUPERVISOR


def test_seed_fleet_coexists_with_manually_registered_agents():
    orch = MetaOrchestrator()
    orch.register_agent("Custom Agent", ["custom"])
    seed_fleet(orch)
    names = {a.name for a in orch.agents()}
    assert "Custom Agent" in names
    assert LEADER_NAME in names
    assert len(orch.agents()) == 41
