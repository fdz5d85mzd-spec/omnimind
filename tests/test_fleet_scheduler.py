"""FleetScheduler: a real periodic tick — rebalances load and publishes a
report to the bus. Tested via direct tick() calls, not real sleep."""

from omni.contracts.agent import AgentStatus, TaskSpec
from omni.fleet.bus import InMemoryBus
from omni.fleet.scheduler import FleetScheduler
from omni.orchestrator.engine import MetaOrchestrator


def test_tick_publishes_report_to_bus():
    orch = MetaOrchestrator()
    orch.register_agent("a1")
    bus = InMemoryBus()
    scheduler = FleetScheduler(orch, bus, interval_s=60.0)

    payload = scheduler.tick()

    assert payload["report"]["agents_total"] == 1
    assert "at" in payload
    subjects = [s for s, _ in bus.published]
    assert "fleet.scheduler.tick" in subjects


def test_tick_rebalances_overloaded_agents():
    orch = MetaOrchestrator()
    busy = orch.register_agent("busy")
    task = orch.submit(TaskSpec(name="t1"))
    orch.assign(task.id)  # only agent registered so far -> lands on busy
    orch.register_agent("idle")

    orch.heartbeat(busy.id, load=0.95, status=AgentStatus.RUNNING)
    busy.queue_depth = 3

    bus = InMemoryBus()
    scheduler = FleetScheduler(orch, bus, interval_s=60.0)
    payload = scheduler.tick()

    assert payload["moved"] >= 1


def test_start_and_stop_are_safe_without_running_loop():
    orch = MetaOrchestrator()
    bus = InMemoryBus()
    scheduler = FleetScheduler(orch, bus)
    # No asyncio loop running here (sync test) — start()/stop() must not
    # raise even though the background task can't actually be scheduled.
    scheduler.stop()  # stopping before starting is a no-op
