# OmniMind

> An autonomous, self-evolving AI operating system. It thinks. It remembers. It
> collaborates. It builds. It improves. It evolves. Forever.

**v0.5.0** — adds the **fleet message bus**: `InMemoryBus` (default) and
`NatsBus` (real NATS server, auto-selected via `NATS_URL`), with `FleetNode`
publishing `announce` / `leader.elected` / `queue.enqueued` / `queue.leased`
events in real time alongside the existing polling storage. (v0.4.0: M5b
sandboxed dry-run execution + ML failure prediction; v0.3.0: M7b mutation
executor, knowledge fusion, skill sharing, k8s/Helm assets; v0.2.0: M4b
remote skills + permission guard, M6b metrics store + replay ledger, M8
fleet, SDK + CLI.)

OmniMind is **not** a chatbot and **not** a single agent. It is a platform of
cooperating subsystems that supervise, evaluate, constrain, remember,
simulate, visualize, and evolve an ecosystem of agents.

```
┌────────────────────────────────────────────────────────────────────┐
│                        OmniMind Platform (v0.3.0)                  │
├──────────────┬──────────────┬──────────────┬───────────────────────┤
│ Meta-        │ Dynamic      │ Simulation   │ Learning &            │
│ Orchestrator │ Skill        │ Sandbox      │ Evaluation Pipeline   │
│              │ Marketplace  │              │                       │
├──────────────┼──────────────┼──────────────┼───────────────────────┤
│ Policy       │ Versioned    │ Digital      │ Self Evolution        │
│ Engine       │ Memory       │ Twin         │ Engine (+mutations)   │
├──────────────┴──────────────┴──────────────┴───────────────────────┤
│  Audit & Replay · Fleet (M8) · Fusion · SDK · CLI · k8s/Helm       │
└────────────────────────────────────────────────────────────────────┘
```

## Quickstart

```bash
# install (exposes the omnimind CLI)
pip install -e ".[dev]"

# run the whole platform in-process as a demo (no server needed)
omnimind demo

# run the test suite (all modules must pass)
python -m pytest

# launch the control plane
uvicorn omni.api.main:app --reload --port 8000
# → http://127.0.0.1:8000/docs

# or run the whole platform in containers
docker compose up --build

# speak to the control plane from the CLI
omnimind --url http://127.0.0.1:8000 twin
omnimind policy --action agent.spawn --role operator --risk low
omnimind memory --key routing.policy --value '{"region":"eu"}' --agent cli --reason "initial"
omnimind simulate --action "deploy v0.2.0" --domain deploy --params '{"size":0.5}'
# M5b: dry-run a plan with zero side effects, and predict its failure risk
omnimind dryrun --domain db_migration --plan '{"name":"m","steps":[{"name":"a","effects":["plan.validate"],"duration_ms":5}]}'
omnimind predict --domain deploy --plan-file plan.json
omnimind fleet --capacity 64
```

## What is implemented vs. scaffolded

| Subsystem | Status | Core capabilities |
|---|---|---|
| Policy Engine | **IMPLEMENTED + tested** | RBAC + ABAC evaluation, default-deny, risk gating, approval chains, cost/rate limits, emergency lockdown, **replayable decision log** |
| Versioned Memory | **IMPLEMENTED + tested** | Immutable append-only versions, diff, rollback-as-new-version, branching, time-travel snapshots, SQLite persistence, **MemoryFuser knowledge fusion** |
| Meta-Orchestrator | **IMPLEMENTED + tested** | Scoring assignment, bottleneck & duplicate detection, auto spawn/terminate, load balancing, EWMA workload prediction, model selection, fleet workload stats |
| Skill Marketplace | **IMPLEMENTED + tested** | Publish/discover/install/upgrade/remove/rate/recommend; 6-kind taxonomy; **remote skill sync (M4b)** + **permission guard (M4b)** + **sharing via export/import** |
| Simulation Sandbox | **IMPLEMENTED + tested** | Risk estimation, rollback plans, confidence score, approval gating; **M5b: sandboxed dry-run (structural zero side effects, effect allow-list, hard budgets) + ML failure predictor (scikit-learn with pure-Python fallback)** |
| Learning & Evaluation | **IMPLEMENTED + tested** | 12 metric dimensions, trend regression, improvement reports, **persistent EvaluationStore (M6b)** |
| Audit & Replay | **IMPLEMENTED + tested (M6b)** | Append-only ReplayLedger: every policy decision is recorded and replayable |
| Digital Twin | **IMPLEMENTED + tested** | Live snapshot builder + **decision replay endpoint** (agents/tasks/skills graph, costs, model usage, queues, errors) |
| Self Evolution Engine | **IMPLEMENTED + tested** | Proposals, before/after measurement, adopt-if-gain gate, negative-evidence ledger, **autonomous mutation executor (M7b)** — adopted proposals mutate routing/config/prompts/memory, policy-gated, reversible |
| Distributed Fleet | **IMPLEMENTED + tested (M8)** | Leader election, registry announcements, shared task queue, workload stats; **PostgresFleetStorage** adapter; **k8s manifests + Helm chart** |
| Client SDK & CLI | **IMPLEMENTED + tested** | `OmniClient` (policy/memory/tasks/skills/simulation/twin/replay) + `omnimind` CLI with in-process `demo` |

## API tour

```bash
# policy decision
curl -s localhost:8000/policy/evaluate -H 'content-type: application/json' -d '{
  "principal": {"id": "ops-1", "roles": ["operator"], "attributes": {}},
  "action": "agent.spawn",
  "resource": {"type": "agent", "attributes": {"risk_level": "medium"}}}'

# write an immutable memory version
curl -s localhost:8000/memory/write -H 'content-type: application/json' -d '{
  "key": "routing.policy", "value": {"region": "eu", "ttl": 3600},
  "agent_id": "agt_01", "reason": "initial routing rule"}'

# simulate a deploy before executing it (confidence gate + rollback plan)
curl -s localhost:8000/simulation/run -H 'content-type: application/json' -d '{
  "action": "deploy v0.2.0", "domain": "deploy", "params": {"size": 0.5}}'

# M5b: dry-run a plan (zero side effects) and ML-predict its failure risk
curl -s localhost:8000/simulation/dryrun -H 'content-type: application/json' -d '{"domain":"db_migration","plan":{"name":"m","steps":[{"name":"a","effects":["plan.validate"],"duration_ms":5}]}}'
curl -s localhost:8000/simulation/predict -H 'content-type: application/json' -d '{"domain":"db_migration","plan":{"name":"m","params":{"size":2.5,"reversible":false},"steps":[{"name":"a","effects":["migration.apply"],"duration_ms":5}]}}'

# sync a remote skill registry (M4b)
curl -s localhost:8000/marketplace/remote/register -H 'content-type: application/json' -d '{
  "url": "https://registry.example/skills/code-gen.json"}'

# replay every policy decision ever made (M6b)
curl -s localhost:8000/audit/replay?subsystem=policy

# apply an adopted evolution proposal as a live mutation (M7b)
curl -s localhost:8000/evolution/apply -H 'content-type: application/json' -d '{
  "proposal_id": "ev_...", "target": "routing",
  "mutation": {"set": {"routing.mode": "cost"}}}'

# fleet: elect a leader, enqueue, adopt (M8)
curl -s -X POST localhost:8000/fleet/elect
curl -s -X POST localhost:8000/fleet/enqueue -H 'content-type: application/json' -d '{"name":"job-1"}'

# see the whole civilization from the twin
curl -s localhost:8000/twin/snapshot
```

## Layout

```
omni/
├── contracts/    # typed schemas shared by every subsystem (the "constitution")
├── policy/       # Policy Engine (+ optional ReplayLedger wiring)
├── memory/       # Versioned Memory (+ MemoryFuser knowledge fusion)
├── orchestrator/ # Meta-Orchestrator
├── marketplace/  # Skill Marketplace + remote sync + permission guard + sharing
├── simulation/   # Simulation Sandbox (engine + M5b dry-run runner + ML predictor)
├── learning/     # Learning Pipeline + persistent EvaluationStore
├── audit/        # ReplayLedger (append-only audit & replay)
├── fleet/        # Distributed fleet: nodes, election, Postgres storage, message bus (InMemoryBus/NatsBus)
├── twin/         # Digital Twin (+ replay)
├── evolution/    # Self Evolution Engine (+ mutation executor M7b)
├── sdk.py        # OmniClient — talk to the control plane programmatically
├── cli.py        # omnimind CLI (demo/policy/memory/twin/simulate/fleet)
└── api/          # FastAPI control plane
deploy/           # k8s manifests (deploy/k8s) + Helm chart (deploy/helm/omnimind)
tests/            # pytest suite (131 tests)
```

See [ROADMAP.md](ROADMAP.md) for milestones M0–M8, [ARCHITECTURE.md](ARCHITECTURE.md)
for the full design, and [CHANGELOG.md](CHANGELOG.md) for the v0.3.0 delta.

## Development

```bash
make install && make test && make run
```

License: Apache-2.0
