"""Digital Twin tests: the snapshot builds a live graph of the whole OS."""

from omni.contracts.agent import TaskSpec
from omni.contracts.skill import SkillKind
from omni.learning.pipeline import LearningPipeline
from omni.marketplace.catalog import SkillCatalog
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.simulation.engine import SimulationSandbox
from omni.twin.builder import TwinBuilder


def test_snapshot_has_live_graph():
    orch = MetaOrchestrator()
    ag = orch.register_agent("w1", skills=["summarize"])
    t = orch.submit(TaskSpec(name="t"))
    orch.assign(t.id)

    mkt = SkillCatalog()
    sk = mkt.publish("summarize", "summarize docs", SkillKind.OFFICIAL, "core", "1.0.0")
    mkt.install(sk.id, ag.id)

    mem = MemoryStore()
    mem.write("policy", {"x": 1}, "agt_1", "seed")

    sim = SimulationSandbox()
    sim.simulate("deploy", "deploy", {"size": 0.5})

    learn = LearningPipeline()
    twin = TwinBuilder(orchestrator=orch, marketplace=mkt, memory=mem, simulation=sim, learning=learn)

    snap = twin.snapshot()
    assert snap["counters"]["agents_total"] == 1
    assert len(snap["task_dependencies"]) == 1
    assert snap["task_dependencies"][0]["relation"] == "executes"
    assert snap["skills"][0]["name"] == "summarize"
    assert snap["memory_keys"][0]["key"] == "policy"
    assert snap["simulations"][0]["domain"] == "deploy"
    assert snap["agent_status_counts"]["running"] == 1
    assert snap["learning"]["samples"] == 0


def test_errors_recorded():
    twin = TwinBuilder()
    twin.record_error("retriever", "timeout")
    twin.record_error("retriever", "timeout 2")
    snap = twin.snapshot()
    assert len(snap["errors"]) == 2
    assert snap["counters"]["errors_last_window"] == 2
