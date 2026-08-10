"""OmniMind control-plane API.

Exposes every subsystem through HTTP:

    /policy/*       - Policy Engine decisions, approvals, lockdown
    /memory/*       - Versioned Memory (immutable, diff, rollback, branch, snapshots)
    /agents, /tasks - Meta-Orchestrator
    /marketplace/*  - Skill Marketplace
    /simulation/*   - Simulation Sandbox
    /learning/*     - Learning & Evaluation Pipeline
    /twin/*         - Digital Twin (/twin/stream is the live WebSocket feed)
    /evolution/*    - Self Evolution Engine
    /dashboard      - Static live-dashboard UI (vanilla JS, consumes /twin/stream)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

from omni import __version__
from omni.agents.fleet_seed import seed_fleet
from omni.agents.reflection import ReflectionScheduler, run_reflection_cycle
from omni.agents.runner import AgentRunner
from omni.audit.ledger import ReplayLedger
from omni.contracts.agent import TaskSpec
from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.contracts.memory import BranchRequest, RollbackRequest
from omni.contracts.policy import LimitPolicy, PolicyRule, Principal, Resource
from omni.contracts.skill import SkillInterface, SkillKind
from omni.evolution.engine import DOMAINS, EvolutionEngine
from omni.evolution.executor import MUTATION_TARGETS, EvolutionExecutor
from omni.fleet.bus import FleetBus, InMemoryBus
from omni.fleet.node import FleetNode
from omni.fleet.protocol import NodeHealth
from omni.fleet.scheduler import FleetScheduler
from omni.fleet.storage import InMemoryFleetStorage
from omni.learning.pipeline import LearningPipeline
from omni.marketplace.catalog import SkillCatalog
from omni.marketplace.remote import RemoteSkillRegistry
from omni.marketplace.security import SkillPermissionGuard
from omni.memory.store import MemoryStore
from omni.orchestrator.engine import MetaOrchestrator
from omni.policy.engine import PolicyEngine, make_seed_rules
from omni.simulation.engine import SIMULATION_DOMAINS, SimulationSandbox
from omni.simulation.predictor import FailurePredictor, generate_synthetic_traces
from omni.simulation.runner import DryRunExecutor
from omni.twin.builder import TwinBuilder
from omni.twin.stream import TwinBroadcaster

app = FastAPI(
    title="OmniMind Control Plane",
    description="An autonomous, self-evolving AI operating system.",
    version=__version__,
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/dashboard", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

# The control plane is called from separate frontend origins (the ops
# dashboard is same-origin under /dashboard, but the public "ask anything"
# UI is its own deployment) — allow any origin rather than hard-coding one,
# since this API exposes no cookie-based auth to protect against CSRF.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------ admin guard
# Mutating admin actions (policy approval, lockdown) are otherwise reachable
# by anyone who knows the API URL — CORS allows any origin and there's no
# session concept at this layer. When ADMIN_API_KEY is set, these endpoints
# require it via X-Admin-Key; a caller (e.g. the frontend's admin dashboard)
# holds it server-side only, never in the browser. Left unset, the
# endpoints behave exactly as before (open) — set it before treating this
# as real access control.
def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ADMIN_API_KEY")
    if expected and x_admin_key != expected:
        raise HTTPException(403, "invalid or missing X-Admin-Key")


# ------------------------------------------------------------ subsystems
ledger = ReplayLedger()
policy = PolicyEngine(make_seed_rules(), ledger=ledger)
memory = MemoryStore()
orchestrator = MetaOrchestrator()
seed_fleet(orchestrator)  # 40 named agents + one leader, idempotent by name
marketplace = SkillCatalog()
remote_registry = RemoteSkillRegistry(marketplace)
skill_guard = SkillPermissionGuard(policy=policy)
simulation = SimulationSandbox()
dryrun = DryRunExecutor()
predictor = FailurePredictor(backend="auto")
predictor.train(generate_synthetic_traces(n=120, seed=7))  # seed so /simulation/predict works out of the box
learning = LearningPipeline()
evolution = EvolutionEngine(learning=learning)
executor = EvolutionExecutor(
    evolution=evolution,
    memory=memory,
    ledger=ledger,
    policy=policy,
    config={},
)


def _make_fleet_bus() -> tuple[FleetBus, str, str | None]:
    """NatsBus when NATS_URL is configured and reachable; InMemoryBus
    otherwise — a misconfigured/unreachable NATS server degrades the fleet
    to polling-only rather than crashing the control plane at startup."""
    url = os.environ.get("NATS_URL")
    if not url:
        return InMemoryBus(), "in_memory", None
    try:
        from omni.fleet.bus import NatsBus

        return NatsBus(url), "nats", None
    except Exception as e:  # pragma: no cover - exercised only with NATS_URL set
        return InMemoryBus(), "in_memory", f"NATS_URL set but connect failed, fell back: {e}"


fleet_bus, fleet_bus_backend, fleet_bus_fallback_reason = _make_fleet_bus()
node = FleetNode(storage=InMemoryFleetStorage(), capacity=64, bus=fleet_bus)
twin = TwinBuilder(
    orchestrator=orchestrator,
    marketplace=marketplace,
    memory=memory,
    simulation=simulation,
    learning=learning,
    ledger=ledger,
)

agent_runner = AgentRunner(policy=policy, memory=memory, orchestrator=orchestrator, bus=fleet_bus)

# Live twin stream (/twin/stream): every bus event — fleet coordination
# AND agent runs — is forwarded to connected WebSocket clients as it
# happens; a periodic snapshot fills the gaps so a client sees state even
# when nothing else is happening.
twin_broadcaster = TwinBroadcaster()
fleet_bus.subscribe(
    ">",
    lambda subject, payload: twin_broadcaster.broadcast({"type": "fleet_event", "subject": subject, "payload": payload}),
)

# Every 60s: rebalance queued work across the fleet and publish a real
# tick (report + move count) to the bus — visible live in Mission
# Control. This is what makes the fleet autonomous rather than just a
# static roster; it does real work on a real clock, not a fake heartbeat.
fleet_scheduler = FleetScheduler(orchestrator, fleet_bus, interval_s=60.0)

# Every SELF_REVIEW_INTERVAL_S (default 6h): the Learning Agent looks at
# real fleet/task/policy metrics and, when something concrete stands out,
# proposes an improvement through the Evolution Engine. It only ever
# proposes — turning a proposal into a live change still needs a human
# with the system.admin role to adopt and apply it via /evolution/apply.
reflection_scheduler = ReflectionScheduler(
    policy, memory, orchestrator, evolution, bus=fleet_bus,
    interval_s=float(os.environ.get("SELF_REVIEW_INTERVAL_S", 21600)),
)


@app.on_event("startup")
async def _start_fleet_scheduler() -> None:
    fleet_scheduler.start()
    reflection_scheduler.start()


@app.on_event("shutdown")
async def _stop_fleet_scheduler() -> None:
    fleet_scheduler.stop()
    reflection_scheduler.stop()


@app.get("/")
def root() -> dict:
    return {
        "name": "OmniMind",
        "version": __version__,
        "subsystems": sorted(
            ["policy", "memory", "agents", "tasks", "marketplace", "simulation", "learning", "twin", "evolution", "fleet", "sdk"]
        ),
        "docs": "/docs",
    }


@app.get("/integrations/status")
def integrations_status() -> dict:
    """Which external integrations are actually configured — presence
    only, never the key itself. Anthropic wins if both LLM keys are set
    (matches omni.agents.llm's own selection order)."""
    llm_provider = (
        "anthropic"
        if os.environ.get("ANTHROPIC_API_KEY")
        else "openai"
        if os.environ.get("OPENAI_API_KEY")
        else None
    )
    return {
        "llm_provider": llm_provider,
        "admin_api_key_configured": bool(os.environ.get("ADMIN_API_KEY")),
        "nats_configured": bool(os.environ.get("NATS_URL")),
    }


# ------------------------------------------------------------ request bodies
class PolicyEvaluateBody(BaseModel):
    principal: Principal
    action: str
    resource: Resource | None = None
    cost: float = 0.0
    calls: int = 1


class MemoryWriteBody(BaseModel):
    key: str
    value: dict
    agent_id: str
    reason: str
    branch: str = "main"


class SkillPublishBody(BaseModel):
    name: str
    description: str = ""
    kind: SkillKind = SkillKind.COMMUNITY
    author: str = "system"
    version: str
    interface: SkillInterface | None = None
    tags: list[str] = Field(default_factory=list)


class SimulationRunBody(BaseModel):
    action: str
    domain: str
    params: dict | None = None


class DryRunPlanBody(BaseModel):
    plan: dict
    domain: str = "generic"
    max_steps: int | None = None
    timeout_s: float | None = None
    max_effects: int | None = None


class PredictorTrainBody(BaseModel):
    traces: list[dict]


class PredictBody(BaseModel):
    plan: dict
    domain: str = "generic"


class LearningIngestBody(BaseModel):
    task_id: str
    agent_id: str
    task_type: str = "generic"
    metrics: MetricBundle = Field(default_factory=MetricBundle)
    summary: str = ""


class AgentRegisterBody(BaseModel):
    name: str
    skills: list[str] = Field(default_factory=list)


class AgentRunBody(BaseModel):
    prompt: str
    session_id: str | None = None


class TaskHeartbeatBody(BaseModel):
    load: float | None = None


class TaskCompleteBody(BaseModel):
    cost: float = 0.0


class RateBody(BaseModel):
    stars: int


class InstallBody(BaseModel):
    skill_id: str
    agent_id: str
    version: str | None = None


class RemoveBody(BaseModel):
    skill_id: str
    agent_id: str


class RemoteSourceBody(BaseModel):
    url: str


class AuthorizeBody(BaseModel):
    principal: Principal
    action: str
    skill_name: str | None = None
    agent_grants: list[str] | None = None


class HeartbeatBody(BaseModel):
    agents_running: int = 0
    agents_total: int = 0
    avg_load: float = 0.0
    queue_depth: int = 0
    cost_spent: float = 0.0
    ewma_rate: float = 0.0
    uptime_s: float = 0.0


class FleetEnqueueBody(BaseModel):
    name: str
    signature: str = ""
    required_skills: list[str] = Field(default_factory=list)
    priority: int = 0
    payload: dict = Field(default_factory=dict)


class MutationApplyBody(BaseModel):
    proposal_id: str
    target: str
    mutation: dict


class MutationRevertBody(BaseModel):
    mutation_id: str


# ================================================================ POLICY
@app.post("/policy/seed")
def policy_seed() -> dict:
    for rule in make_seed_rules():
        policy.add_rule(rule)
    return {"seeded": len(make_seed_rules())}


@app.get("/policy/rules")
def policy_rules() -> list[PolicyRule]:
    return policy._rules


@app.post("/policy/evaluate")
def policy_evaluate(body: PolicyEvaluateBody):
    return policy.evaluate(body.principal, body.action, body.resource, body.cost, body.calls)


@app.post("/policy/approve/{decision_id}", dependencies=[Depends(require_admin_key)])
def policy_approve(decision_id: str, approver_role: str) -> dict:
    try:
        return policy.approve(decision_id, approver_role).model_dump()
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/policy/pending")
def policy_pending() -> list[dict]:
    return [d.model_dump() for d in policy.pending_approvals()]


@app.get("/policy/log")
def policy_log(limit: int = 100) -> list[dict]:
    return [d.model_dump() for d in policy.decision_log(limit)]


@app.post("/policy/limits")
def policy_add_limit(limit: LimitPolicy) -> LimitPolicy:
    return policy.add_limit(limit)


@app.post("/policy/lockdown", dependencies=[Depends(require_admin_key)])
def policy_lockdown(enabled: bool) -> dict:
    return {"lockdown": policy.set_lockdown(enabled)}


# ================================================================ MEMORY
@app.post("/memory/write")
def memory_write(body: MemoryWriteBody):
    return memory.write(body.key, body.value, body.agent_id, body.reason, body.branch).model_dump()


@app.get("/memory/read")
def memory_read(key: str, branch: str = "main", version: int | None = None):
    entry = memory.read(key, branch, version)
    if entry is None:
        raise HTTPException(404, f"no version of '{key}' on branch '{branch}'")
    return entry.model_dump()


@app.get("/memory/history")
def memory_history(key: str, branch: str = "main"):
    return [e.model_dump() for e in memory.history(key, branch)]


@app.get("/memory/diff")
def memory_diff(key: str, branch: str = "main", from_version: int | None = None, to_version: int | None = None):
    d = memory.diff(key, branch, from_version, to_version)
    if d is None:
        raise HTTPException(404, f"cannot diff '{key}' on branch '{branch}'")
    return d.model_dump()


@app.post("/memory/rollback")
def memory_rollback(request: RollbackRequest):
    try:
        return memory.rollback(request).model_dump()
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/memory/branch")
def memory_branch(request: BranchRequest):
    try:
        return memory.branch(request).model_dump()
    except KeyError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/memory/snapshot")
def memory_snapshot(asof: str | None = None) -> dict:
    return memory.snapshot_asof(asof) if asof else memory.snapshot()


# ================================================================ ORCHESTRATOR
@app.post("/agents/register")
def agent_register(body: AgentRegisterBody):
    return orchestrator.register_agent(body.name, body.skills).model_dump()


@app.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str, body: TaskHeartbeatBody | None = None):
    try:
        return orchestrator.heartbeat(agent_id, None if body is None else body.load).model_dump()
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/agents")
def agents_list() -> list[dict]:
    return [a.model_dump() for a in orchestrator.agents()]


@app.post("/tasks/submit")
def task_submit(task: TaskSpec):
    return orchestrator.submit(task).model_dump()


@app.post("/tasks/{task_id}/assign")
def task_assign(task_id: str):
    agent_id = orchestrator.assign(task_id)
    if agent_id is None:
        raise HTTPException(409, f"no capable agent for task '{task_id}'")
    return {"task_id": task_id, "assignee": agent_id}


@app.post("/tasks/{task_id}/complete")
def task_complete(task_id: str, body: TaskCompleteBody | None = None):
    return orchestrator.complete(task_id, cost=0.0 if body is None else body.cost).model_dump()


@app.get("/orchestrator/report")
def orchestrator_report() -> dict:
    return orchestrator.report()


@app.post("/orchestrator/balance")
def orchestrator_balance() -> list[dict]:
    return [r.__dict__ for r in orchestrator.balance()]


@app.post("/orchestrator/spawn")
def orchestrator_spawn() -> list[str]:
    return orchestrator.spawn_if_needed()


@app.post("/orchestrator/terminate-idle")
def orchestrator_terminate_idle() -> list[str]:
    return orchestrator.terminate_idle()


@app.get("/orchestrator/predict")
def orchestrator_predict(horizon_minutes: float = 10.0) -> dict:
    return {"predicted_tasks": orchestrator.predict_next_tasks(horizon_minutes)}


# ================================================================ PUBLIC AGENT
# The public "ask anything" entry point (distinct from /agents/* above,
# which is the Meta-Orchestrator's internal fleet registry). Every call
# runs through the real Policy Engine, Meta-Orchestrator and Versioned
# Memory, publishing each stage live to /twin/stream — see omni/agents/.
@app.post("/agent/run")
def agent_run(body: AgentRunBody) -> dict:
    result = agent_runner.run(body.prompt, session_id=body.session_id or "anonymous")
    return result.to_dict()


@app.post("/agent/run/stream")
def agent_run_stream(body: AgentRunBody):
    """Server-Sent Events: the answer arrives as text deltas, not one
    blocking response — this is what the chat UI renders live."""

    def events():
        import json as _json

        for event in agent_runner.run_stream(body.prompt, session_id=body.session_id or "anonymous"):
            yield f"data: {_json.dumps(event)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


# ================================================================ MARKETPLACE
@app.post("/marketplace/publish")
def skill_publish(body: SkillPublishBody):
    return marketplace.publish(
        body.name, body.description, body.kind, body.author, body.version, body.interface, body.tags
    ).model_dump()


@app.get("/marketplace/search")
def skill_search(query: str = "", kind: SkillKind | None = None):
    return [s.model_dump() for s in marketplace.search(query, kind)]


@app.post("/marketplace/install")
def skill_install(body: InstallBody):
    skill = marketplace.install(body.skill_id, body.agent_id, body.version)
    if skill is None:
        raise HTTPException(404, f"no skill '{body.skill_id}'")
    return skill.model_dump()


@app.post("/marketplace/remove")
def skill_remove(body: RemoveBody) -> dict:
    return {"removed": marketplace.remove(body.skill_id, body.agent_id)}


@app.post("/marketplace/rate")
def skill_rate(skill_id: str, body: RateBody) -> dict:
    return {"new_rating": marketplace.rate(skill_id, body.stars)}


@app.get("/marketplace/recommend")
def skill_recommend(agent_id: str) -> list[dict]:
    return [s.model_dump() for s in marketplace.recommend(agent_id)]


# remote skill sync (M4b)
@app.post("/marketplace/remote/register")
def remote_register(body: RemoteSourceBody) -> dict:
    remote_registry.register_source(body.url)
    return {"sources": remote_registry.sources()}


@app.post("/marketplace/remote/sync")
def remote_sync(body: RemoteSourceBody) -> dict:
    manifest = remote_registry.sync(body.url)
    if manifest is None:
        raise HTTPException(404, f"no new versions from '{body.url}'")
    return manifest.model_dump()


@app.get("/marketplace/remote/sources")
def remote_sources() -> dict:
    return {"sources": remote_registry.sources()}


# skill permission guard (M4b)
@app.post("/marketplace/authorize")
def marketplace_authorize(body: AuthorizeBody) -> dict:
    skill = None
    if body.skill_name:
        found = marketplace.search(body.skill_name)
        skill = found[0] if found else None
    return skill_guard.authorize(body.principal, body.action, skill, body.agent_grants)


# ================================================================ SIMULATION
@app.post("/simulation/run")
def simulation_run(body: SimulationRunBody):
    try:
        return simulation.simulate(body.action, body.domain, body.params).model_dump()
    except KeyError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/simulation/domains")
def simulation_domains() -> list[str]:
    return sorted(SIMULATION_DOMAINS)


@app.post("/simulation/{simulation_id}/approve")
def simulation_approve(simulation_id: str, approver: str):
    try:
        return simulation.approve(simulation_id, approver).model_dump()
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/simulation/history")
def simulation_history() -> list[dict]:
    return [s.model_dump() for s in simulation.history()]


# M5b: sandboxed dry-run execution + ML failure prediction
@app.post("/simulation/dryrun")
def simulation_dryrun(body: DryRunPlanBody):
    try:
        kwargs = {}
        if body.max_steps is not None:
            kwargs["max_steps"] = body.max_steps
        if body.timeout_s is not None:
            kwargs["timeout_s"] = body.timeout_s
        if body.max_effects is not None:
            kwargs["max_effects"] = body.max_effects
        executor = DryRunExecutor(**kwargs)
        return executor.run(body.plan, body.domain).to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/simulation/predictor/status")
def predictor_status() -> dict:
    return predictor.model_info()


@app.post("/simulation/predictor/train")
def predictor_train(body: PredictorTrainBody):
    try:
        predictor.train(body.traces)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return predictor.model_info()


@app.post("/simulation/predict")
def simulation_predict(body: PredictBody):
    return predictor.predict_plan(body.plan, body.domain)


# ================================================================ LEARNING
@app.post("/learning/ingest")
def learning_ingest(body: LearningIngestBody):
    eval_ = Evaluation(
        task_id=body.task_id,
        agent_id=body.agent_id,
        task_type=body.task_type,
        metrics=body.metrics,
        summary=body.summary,
    )
    return learning.ingest(eval_).model_dump()


@app.get("/learning/aggregate")
def learning_aggregate(task_type: str | None = None) -> dict:
    return learning.aggregate(task_type)


@app.get("/learning/trends")
def learning_trends(task_type: str | None = None) -> dict:
    return learning.trends(task_type)


@app.get("/learning/report")
def learning_report() -> dict:
    return learning.improvement_report()


@app.post("/learning/reflect")
def learning_reflect() -> dict:
    """Run one self-review cycle on demand (the same cycle ReflectionScheduler
    runs every SELF_REVIEW_INTERVAL_S) -- proposes at most one improvement
    via the Evolution Engine; never applies anything itself."""
    result = run_reflection_cycle(policy, memory, orchestrator, evolution, bus=fleet_bus)
    return vars(result)


# ================================================================ TWIN
@app.get("/twin/snapshot")
def twin_snapshot() -> dict:
    return twin.snapshot()


@app.get("/twin/replay")
def twin_replay(subject: str | None = None, subsystem: str | None = None, limit: int = 100) -> list[dict]:
    return twin.replay(subject=subject, subsystem=subsystem, limit=limit)


@app.get("/audit/replay")
def audit_replay(subject: str | None = None, subsystem: str | None = None, limit: int = 100) -> list[dict]:
    """Alias to the same immutable ledger — every decision is replayable."""
    return ledger.replay(subject=subject, subsystem=subsystem, limit=limit)


@app.post("/twin/error")
def twin_error(source: str, message: str) -> dict:
    twin.record_error(source, message)
    return {"recorded": True}


TWIN_STREAM_HEARTBEAT_S = 2.0  # snapshot fallback interval when the fleet is quiet


@app.websocket("/twin/stream")
async def twin_stream(websocket: WebSocket) -> None:
    """Live feed: an initial full snapshot, then every fleet bus event
    (announce/leader.elected/queue.enqueued/queue.leased) as it happens,
    with a periodic snapshot as a heartbeat when nothing else occurs.
    One connection == one broadcaster subscription; dropped on disconnect."""
    await websocket.accept()
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def sink(message: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, message)

    sub_id = twin_broadcaster.subscribe(sink)
    try:
        await websocket.send_json({"type": "snapshot", "data": twin.snapshot()})
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=TWIN_STREAM_HEARTBEAT_S)
                await websocket.send_json(message)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "snapshot", "data": twin.snapshot()})
    except WebSocketDisconnect:
        pass
    finally:
        twin_broadcaster.unsubscribe(sub_id)


@app.get("/twin/stream/subscribers")
def twin_stream_subscribers() -> dict:
    return {"connected": twin_broadcaster.subscriber_count()}


# ================================================================ FLEET (M8)
@app.get("/fleet/status")
def fleet_status() -> dict:
    return {
        "node": node.id,
        "registered": node.is_registered(),
        "peers": [n.get("node") for n in node.peers()],
        "leader": node.storage.get_leader(),
        "is_leader": node.is_leader(),
    }


@app.post("/fleet/announce")
def fleet_announce(body: HeartbeatBody | None = None) -> dict:
    health = None
    if body is not None:
        health = NodeHealth(node=node.id, **body.model_dump())
    return node.announce(health)


@app.post("/fleet/elect")
def fleet_elect() -> dict:
    return node.elect()


@app.post("/fleet/enqueue")
def fleet_enqueue(body: FleetEnqueueBody) -> dict:
    return node.enqueue(body.model_dump())


@app.post("/fleet/adopt")
def fleet_adopt() -> dict:
    task = node.adopt_task()
    if task is None:
        return {"leased": None}
    return {"leased": task}


@app.get("/fleet/leader")
def fleet_leader() -> dict:
    return node.storage.get_leader() or {"leader": None, "term": 0}


@app.get("/fleet/bus/status")
def fleet_bus_status() -> dict:
    return {
        "backend": fleet_bus_backend,  # "nats" or "in_memory"
        "fallback_reason": fleet_bus_fallback_reason,  # set only if NATS_URL was configured but unreachable
        "configured_url": os.environ.get("NATS_URL"),
    }


@app.get("/fleet/bus/events")
def fleet_bus_events(limit: int = 50) -> list[dict]:
    """Recent published fleet events — only available on the in-memory bus
    (a real NATS server has no built-in replay; subscribe for a live feed)."""
    if not isinstance(fleet_bus, InMemoryBus):
        raise HTTPException(400, "event history is only available on the in-memory bus backend")
    return [{"subject": s, "payload": p} for s, p in fleet_bus.published[-limit:]]


# ================================================================ EVOLUTION
@app.get("/evolution/domains")
def evolution_domains() -> list[str]:
    return DOMAINS


@app.post("/evolution/propose")
def evolution_propose(domain: str, title: str, description: str, hypothesis: str = ""):
    try:
        return vars(evolution.propose(domain, title, description, hypothesis))
    except KeyError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/evolution/{proposal_id}/evaluate")
def evolution_evaluate(proposal_id: str, after: MetricBundle):
    try:
        return vars(evolution.evaluate(proposal_id, after))
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/evolution/{proposal_id}/adopt")
def evolution_adopt(proposal_id: str):
    try:
        return vars(evolution.adopt_if_gain(proposal_id))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/evolution/ledger")
def evolution_ledger() -> list[dict]:
    return [vars(p) for p in evolution.ledger()]


@app.get("/evolution/targets")
def evolution_targets() -> list[str]:
    return list(MUTATION_TARGETS)


@app.post("/evolution/apply")
def evolution_apply(body: MutationApplyBody):
    try:
        return vars(executor.apply(body.proposal_id, body.target, body.mutation))
    except (KeyError, ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/evolution/revert")
def evolution_revert(body: MutationRevertBody):
    try:
        return vars(executor.revert(body.mutation_id))
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/evolution/mutations")
def evolution_mutations() -> list[dict]:
    return [vars(r) for r in executor.records()]
