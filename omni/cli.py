"""OmniMind CLI — operate the control plane from a terminal.

The `demo` command runs an in-process demonstration (no server needed);
all other commands speak to a running control plane through the SDK.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from omni import __version__
from omni.sdk import OmniClient


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"omnimind {__version__}")
    return 0


def _cmd_demo(_: argparse.Namespace) -> int:
    """In-process demonstration of the whole platform."""
    from omni.contracts.agent import TaskSpec
    from omni.contracts.evaluation import Evaluation, MetricBundle
    from omni.contracts.policy import Principal, Resource
    from omni.contracts.skill import SkillInterface, SkillKind
    from omni.evolution.engine import EvolutionEngine
    from omni.learning.pipeline import LearningPipeline
    from omni.marketplace.catalog import SkillCatalog
    from omni.memory.store import MemoryStore
    from omni.orchestrator.engine import MetaOrchestrator
    from omni.policy.engine import PolicyEngine, make_seed_rules
    from omni.simulation.engine import SimulationSandbox
    from omni.twin.builder import TwinBuilder

    policy = PolicyEngine(make_seed_rules())
    memory = MemoryStore()
    orch = MetaOrchestrator()
    catalog = SkillCatalog()
    sim = SimulationSandbox()
    learning = LearningPipeline()
    twin = TwinBuilder(orchestrator=orch, marketplace=catalog, memory=memory, simulation=sim, learning=learning)

    # policy
    decision = policy.evaluate(
        Principal(id="ops-1", roles=["operator"]),
        "agent.spawn",
        Resource(type="agent", attributes={"risk_level": "low"}),
    )

    # orchestrator
    agent = orch.register_agent("worker", skills=["summarize"])
    task = orch.submit(TaskSpec(name="summarize docs", required_skills=["summarize"]))
    orch.assign(task.id)

    # memory
    entry = memory.write("routing.policy", {"region": "eu"}, agent.id, "seeded by demo")

    # skill
    skill = catalog.publish("summarize", "summarize docs", SkillKind.OFFICIAL, "core", "1.0.0",
                            interface=SkillInterface(actions=["summarize"]))
    catalog.install(skill.id, agent.id)

    # simulation
    sim_result = sim.simulate("deploy", "deploy", {"size": 0.5})

    # learning + evolution
    learning.ingest(Evaluation(task_id=task.id, agent_id=agent.id, task_type="summary",
                               metrics=MetricBundle(accuracy=0.9, execution_time_ms=120)))
    evo = EvolutionEngine(learning=learning)
    proposal = evo.propose("routing", "cost routing", "route by cost")
    evo.evaluate(proposal.id, MetricBundle(accuracy=0.95, cost=0.05))

    snapshot = twin.snapshot()

    result = {
        "policy": {"allowed": decision.allowed, "rule": decision.matched_rule},
        "orchestrator": orch.report(),
        "memory": {"key": entry.key, "version": entry.version},
        "skills": {"installed": len(catalog.installed_by(agent.id))},
        "simulation": {"confidence": sim_result.confidence, "needs_approval": sim_result.needs_approval},
        "learning_samples": len(learning.evaluations()),
        "evolution": {"proposal": proposal.title, "status": proposal.status},
        "twin_agents": snapshot["counters"]["agents_total"],
        "twin_errors": len(snapshot["errors"]),
    }
    _print(result)
    return 0


def _cmd_policy(args: argparse.Namespace) -> int:
    client = OmniClient(args.url)
    decision = client.evaluate_policy(
        principal={"id": args.principal, "roles": [args.role], "groups": [], "attributes": {}},
        action=args.action,
        resource={"type": args.resource_type, "attributes": {"risk_level": args.risk}},
        cost=args.cost,
    )
    _print(decision)
    return 0


def _cmd_memory(args: argparse.Namespace) -> int:
    client = OmniClient(args.url)
    entry = client.write_memory(args.key, json.loads(args.value), args.agent, args.reason)
    _print(entry)
    return 0


def _cmd_twin(args: argparse.Namespace) -> int:
    client = OmniClient(args.url)
    _print(client.twin_snapshot())
    return 0


def _cmd_simulate(args: argparse.Namespace) -> int:
    client = OmniClient(args.url)
    params = json.loads(args.params) if args.params else None
    _print(client.simulate(args.action, args.domain, params))
    return 0


def _load_plan(args) -> dict:
    if args.plan_file:
        with open(args.plan_file, encoding="utf-8") as fh:
            return json.load(fh)
    if args.plan:
        return json.loads(args.plan)
    raise ValueError("provide --plan (inline JSON) or --plan-file (path)")


def _cmd_dryrun(args: argparse.Namespace) -> int:
    client = OmniClient(args.url)
    plan = _load_plan(args)
    kwargs = {}
    if args.max_steps is not None:
        kwargs["max_steps"] = args.max_steps
    if args.timeout is not None:
        kwargs["timeout_s"] = args.timeout
    if args.max_effects is not None:
        kwargs["max_effects"] = args.max_effects
    _print(client.dry_run(plan, args.domain, **kwargs))
    return 0


def _cmd_predict(args: argparse.Namespace) -> int:
    client = OmniClient(args.url)
    _print(client.predict_failure(_load_plan(args), args.domain))
    return 0


def _cmd_fleet(args: argparse.Namespace) -> int:
    from omni.fleet.node import FleetNode
    from omni.fleet.storage import InMemoryFleetStorage

    node = FleetNode(storage=InMemoryFleetStorage(), node_id_value=args.node or None, capacity=args.capacity)
    node.announce()
    leader = node.elect()
    _print({"node": node.id, "peers_known": len(node.peers()), "leader": leader, "is_leader": node.is_leader()})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnimind", description="OmniMind control CLI")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="control plane base URL")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print version")
    sub.add_parser("demo", help="run an in-process platform demo (no server needed)")

    p_policy = sub.add_parser("policy", help="evaluate a policy decision against the control plane")
    p_policy.add_argument("--action", required=True)
    p_policy.add_argument("--role", default="operator")
    p_policy.add_argument("--principal", default="cli-user")
    p_policy.add_argument("--risk", default="low")
    p_policy.add_argument("--resource-type", default="generic")
    p_policy.add_argument("--cost", type=float, default=0.0)

    p_memory = sub.add_parser("memory", help="write an immutable memory version")
    p_memory.add_argument("--key", required=True)
    p_memory.add_argument("--value", required=True, help='JSON object, e.g. \'{"region":"eu"}\'')
    p_memory.add_argument("--agent", default="cli")
    p_memory.add_argument("--reason", default="written from CLI")

    sub.add_parser("twin", help="print the live Digital Twin snapshot")

    p_sim = sub.add_parser("simulate", help="run a simulation for a critical action")
    p_sim.add_argument("--action", required=True)
    p_sim.add_argument("--domain", required=True)
    p_sim.add_argument("--params", default=None, help="optional JSON object")

    p_dry = sub.add_parser("dryrun", help="dry-run a plan with zero side effects (M5b)")
    p_dry.add_argument("--plan", default=None, help="inline JSON plan")
    p_dry.add_argument("--plan-file", default=None, help="path to JSON plan file")
    p_dry.add_argument("--domain", default="generic")
    p_dry.add_argument("--max-steps", type=int, default=None)
    p_dry.add_argument("--timeout", type=float, default=None)
    p_dry.add_argument("--max-effects", type=int, default=None)

    p_pred = sub.add_parser("predict", help="predict failure risk of a plan (M5b)")
    p_pred.add_argument("--plan", default=None, help="inline JSON plan")
    p_pred.add_argument("--plan-file", default=None, help="path to JSON plan file")
    p_pred.add_argument("--domain", default="generic")

    p_fleet = sub.add_parser("fleet", help="start a local fleet node and run election")
    p_fleet.add_argument("--node", default=None)
    p_fleet.add_argument("--capacity", type=int, default=32)

    return parser


def main_entry(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "version": _cmd_version,
        "demo": _cmd_demo,
        "policy": _cmd_policy,
        "memory": _cmd_memory,
        "twin": _cmd_twin,
        "simulate": _cmd_simulate,
        "dryrun": _cmd_dryrun,
        "predict": _cmd_predict,
        "fleet": _cmd_fleet,
    }
    if args.command is None:
        parser.print_help()
        return 2
    try:
        return handlers[args.command](args)
    except Exception as exc:  # surface cleanly for CLI users
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main_entry())
