# OmniMind — System Architecture

## 1. Mission

OmniMind is an **autonomous AI civilization**, not an assistant. The platform
continuously: thinks (orchestrates), remembers (versioned memory), collaborates
(agents + marketplace), builds (skills + execution), improves (learning +
evolution), and evolves (self-modification gated by measurement). Every change is
**policy-checked** and **simulation-first**; every memory is **immutable and
auditable**; every decision is **replayable and inspectable**.

## 2. Design invariants

1. **Policy first.** No action executes without a Policy Engine decision.
   Default is deny. Every decision is auditable.
2. **Simulate before you touch.** Critical actions (deploy, DB migration, file
   deletion, payments, infra change) require a sandbox simulation with a
   confidence score; below threshold → human approval.
3. **Memory is immutable.** Updates append versions; rollback writes a *new*
   version pointing back. History is never rewritten. Every version records
   *who* changed it, *how*, and *why*.
4. **Everything is measurable.** Every task produces an Evaluation; evaluations
   feed the Learning Pipeline; the pipeline feeds the Evolution Engine; the
   Evolution Engine proposes changes; changes are adopted only on measured gain.
5. **Everything is inspectable.** The Digital Twin exposes a live graph of
   agents, tasks, skills, memory, costs, models, queues, and errors.

## 3. Subsystem overview

```
                     ┌──────────────────────────────┐
                     │        POLICY ENGINE         │
                     │  RBAC · ABAC · limits ·      │
                     │  approval chains · lockdown  │
                     └──────┬──────────────┬────────┘
                            │ evaluate     │ enforce
        ┌───────────────────▼──────────────▼───────────────────┐
        │                   META-ORCHESTRATOR                   │
        │  assign · balance · spawn · terminate · predict ·     │
        │  duplicate detection · model selection                │
        └───┬──────────┬──────────┬──────────┬──────────┬───────┘
            │ skills   │ tasks    │ memory   │ sim      │ eval
   ┌────────▼───┐ ┌─────▼──────┐ ┌─▼─────────┐ ┌─▼────────┴───┐
   │  SKILL     │ │ VERSIONED  │ │ SIMULATION│ │ LEARNING &   │
   │MARKETPLACE │ │  MEMORY    │ │  SANDBOX  │ │ EVALUATION   │
   └────────────┘ └────────────┘ └───────────┘ └──────┬────────┘
                                                      │ trends
   ┌──────────────────────────────────────────────────▼─────────┐
   │                  SELF EVOLUTION ENGINE                      │
   │  proposals → experiment → measure → adopt-if-gain           │
   └─────────────────────────────────────────────────────────────┘
        ▲                                                        │
        └──────────── DIGITAL TWIN (observes every node) ────────┘
```

### 3.1 Meta-Orchestrator (`omni/orchestrator/`)

Monitors every agent (heartbeat, load, queue), measures performance, detects
bottlenecks (load ≥ threshold or queue depth ≥ threshold) and duplicated work
(signature groups), reassigns tasks to balance load, spawns agents under surge,
terminates idle agents, predicts future load with an EWMA arrival-rate model,
and optimizes model selection per task given a catalog of
`(provider, cost, latency, quality, capabilities)`.

Key interfaces:

```
submit(task) → TaskSpec                  # enters pool
assign(task) → agent_id | None           # pluggable ScoringStrategy
heartbeat(agent_id, load, status) → AgentSpec
detect_bottlenecks(thresholds) → [BottleneckView]
detect_duplicates() → [[task_id, ...]]   # identical signatures in flight
spawn_if_needed() → [new agent ids]
terminate_idle(timeout, keep_min) → [terminated ids]
balance() → [Reassignment]               # overloaded → underloaded
predict_next_tasks(horizon_min) → float  # EWMA rate × horizon
choose_model(task, catalog, mode) → ModelOption   # cost|speed|quality|balanced
```

Scoring strategies are pluggable: `BalancedScoring` (default),
`CostOptimizedScoring`, `SpeedOptimizedScoring`.

### 3.2 Dynamic Skill Marketplace (`omni/marketplace/`)

Every capability is a skill; every skill is versioned, installable, upgradable,
removable, ratable, shareable. A skill manifest exposes actions, events,
permissions, configuration, documentation, tests, UI components, and API
endpoints. Skills carry a kind (official / community / private / enterprise /
local / remote) and a version list.

### 3.3 Simulation Sandbox (`omni/simulation/`)

Critical actions first run inside an isolated simulation that estimates risk,
predicts failures and side effects, generates a rollback plan and alternatives,
produces a confidence score, and recommends the safest execution plan. If
confidence < policy threshold (`SIM_CONFIDENCE_THRESHOLD`, default 0.7) or the
risk level is high/critical, execution pauses and requests human approval.

### 3.4 Learning & Evaluation Pipeline (`omni/learning/`)

Every completed task becomes training data. Each Evaluation carries 12 metric
dimensions (execution time, cost, accuracy, reasoning quality, user
satisfaction, code quality, architecture quality, documentation quality, bug
density, security score, performance, maintainability). The pipeline computes
trends (linear regression slope per metric) and emits improvement reports to
drive routing, planning, prompt templates, and collaboration changes.

### 3.5 Policy Engine (`omni/policy/`)

Centralized decision point. Policy rules combine **RBAC** (roles/groups) with
**ABAC** (attribute conditions, e.g. `resource.risk_level ≤ high`), ordered
deny-before-allow, default deny. `LimitPolicy` enforces resource/cost/time
limits on a rolling window per principal, group, or globally. `require_approval`
chains collect approvers; `emergency lockdown` denies everything except
override roles.

### 3.6 Versioned Memory (`omni/memory/`)

Immutable, append-only version store backed by SQLite. Every update creates a
new version recording `agent_id`, `reason`, `parent_version`, `hash`, and
`created_at`. Supports per-key history, structural diffs (add/remove/replace),
rollback (new version whose value equals an old one), branching (copy a version
into a new branch as v1 with provenance), whole-store snapshots, and time-travel
(`snapshot_asof`). Conflict rule: the only writer to a `(key, branch)` append
log is the store itself; branch isolation is enforced by primary key.

### 3.7 Digital Twin (`omni/twin/`)

A live, replayable model of the whole operating system: nodes for agents,
tasks, skills, memory keys, and edges for assignments, dependencies, and skill
installations; plus stats for costs, model usage, queues, and errors. Every
decision can be replayed from the orchestrator logs; every instruction chain is
inspectable via the twin snapshot.

### 3.8 Self Evolution Engine (`omni/evolution/`)

Continuously generates proposals (new architectures, better workflows, improved
prompts, memory organization, routing, agents, plugins, UI, APIs, infra,
documentation). Every proposal is measured before/after against the Learning
Pipeline; **only proposals with measurable gains are adopted**. Rejected
experiments stay in the ledger as negative evidence.

## 4. Shared contracts (`omni/contracts/`)

All subsystems import these typed schemas; nothing redefines them:

| Module | Types |
|---|---|
| `agent.py` | AgentSpec, AgentType, AgentStatus, TaskSpec, TaskStatus |
| `skill.py` | SkillManifest, SkillVersion, SkillInterface, SkillKind |
| `policy.py` | Principal, Resource, PolicyRule, ABACCondition, LimitPolicy, PolicyDecision, ApprovalStatus, LimitStatus, RiskLevel |
| `memory.py` | MemoryEntry, MemoryDiff, MemoryDiffItem, MemoryDiffOp, RollbackRequest, BranchRequest |
| `simulation.py` | SimulationResult |
| `evaluation.py` | MetricBundle, Evaluation |

## 5. Security model

- **Default deny**: unmatched actions are denied with a recorded reason.
- **Approval chains**: destructive/expensive actions require N approvers unless
  the principal carries the `approval.exempt` role.
- **Lockdown**: emergency switch that denies all non-`system.admin` actions.
- **Immutable audit trail**: memory versions and policy decisions are
  append-only (decisions are logged by the engine); nothing is ever mutated in
  place.

## 6. Deployment

- Single control plane process (FastAPI + uvicorn), SQLite storage, in-memory
  registries; containers via `Dockerfile` / `docker-compose.yml`.
- Planned evolution (M6+): Postgres for memory, NATS/Redis for inter-agent
  messaging, worker fleet with a leader-elected orchestrator. See ROADMAP.

## 7. Reference

- [ROADMAP.md](ROADMAP.md) — milestones M0–M8, deliverables, acceptance criteria
- [README.md](README.md) — quickstart, module matrix, API tour
