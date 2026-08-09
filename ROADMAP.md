# OmniMind — Implementation Roadmap

Status legend: ✅ done · 🚧 in progress · ⬜ planned

## M0 — Foundation & Contracts (✅ current milestone)

**Scope:** repository, docs, shared typed contracts, test harness, CI, deployment assets.
**Deliverables:** `omni/contracts/*` (Agent, Skill, Policy, Memory, Simulation, Evaluation schemas); pyproject/Makefile/Dockerfile/docker-compose/CI; ARCHITECTURE + ROADMAP.
**Acceptance:** `python -m pytest` green on harness; images build; contracts importable by all modules.
**Status:** ✅ complete.

## M1 — Policy Engine (✅)

**Scope:** RBAC+ABAC evaluation, default deny, ordered deny-before-allow, risk gating, approval chains, rolling-window limits (cost/rate/resource), emergency lockdown, decision log.
**Deliverables:** `omni/policy/engine.py`, seed rules, `/policy/*` API, `tests/test_policy.py`.
**Acceptance:** all policy tests pass; evaluated decisions carry matched rule, reasons, limits, approval state.
**Status:** ✅ complete.

## M2 — Versioned Memory (✅)

**Scope:** immutable append-only store (SQLite), write/read/history, structural diff, rollback-as-new-version, branching with provenance, snapshot & time-travel, who/what/why metadata on every version.
**Deliverables:** `omni/memory/{store,diff}.py`, `/memory/*` API, `tests/test_memory.py`.
**Acceptance:** old versions byte-stable after updates; diff items correct; rollback creates new version; branches isolated.
**Status:** ✅ complete.

## M3 — Meta-Orchestrator (✅)

**Scope:** agent registry + heartbeat, pluggable scoring assignment, bottleneck & duplicate detection, auto spawn/terminate, load balancing, EWMA workload prediction, model selection optimization.
**Deliverables:** `omni/orchestrator/*`, `/agents/*` + `/tasks/*` API, `tests/test_orchestrator.py`.
**Acceptance:** orchestration tests pass; scheduler demo from REPL.
**Status:** ✅ complete.

## M4 — Dynamic Skill Marketplace (✅ M4b complete)

**Scope:** full catalog lifecycle (publish/discover/install/upgrade/remove/rate/recommend/share), kind taxonomy, version systems, remote skills.
**Deliverables:** `omni/marketplace/{catalog,remote,security}.py`, `/marketplace/*` + remote + authorize API, tests.
**Status:** ✅ complete — **M4b (remote skill sync + permission guard)** implemented: `RemoteSkillRegistry` syncs versioned manifests from arbitrary URLs with provenance (`source_url`, `last_synced`), `SkillPermissionGuard` enforces declared skill permissions against agent grants with central-policy fallback. Runtime UI component registry delivery remains out of scope of the core (note only).

## M5 — Simulation Sandbox (✅ complete)

**Scope:** risk estimator, failure/side-effect predictors, rollback plan generator, alternatives, confidence score, approval gating, policy threshold; **M5b: sandboxed dry-run execution + ML failure prediction**.
**Deliverables:** `omni/simulation/{engine,runner,predictor}.py`, `/simulation/*` API (run, dryrun, predict, predictor), CLI `simulate | dryrun | predict`, tests (8+7+5 new).
**Status:** ✅ complete — heuristic engine + **M5b**: `DryRunExecutor`/`NoopEffectBackend` (structural zero-side-effect execution with an explicit effect allow-list and hard step/time/effect budgets) and `FailurePredictor` (scikit-learn RandomForest, auto-detected, with a pure-Python logistic fallback; `generate_synthetic_traces` seeds it; save/load persistence). Prediction improves as real execution traces are ingested.

## M6 — Learning & Evaluation Pipeline + Digital Twin (✅ M6b complete)

**Scope:** evaluation ingestion (12 metrics), trend regression, improvement reports; live twin graph builder with replay.
**Deliverables:** `omni/learning/{pipeline,store,aggregate}.py`, `omni/audit/ledger.py`, `omni/twin/builder.py`, `/learning/*` + `/twin/*` + `/audit/replay` API, tests.
**Status:** ✅ complete — **M6b (persistent store + replay ledger)** implemented: `EvaluationStore` (SQLite) survives restarts and drives pipeline history/trends; `ReplayLedger` records every policy decision (append-only) and powers `/twin/replay` + `/audit/replay`. Per-metric trend series streaming remains an observability nicety, not a correctness gap.

## M7 — Self Evolution Engine (✅ M7b complete)

**Scope:** proposal generation, before/after experiments, adopt-if-gain gate, rejected-experiment ledger; self-modifying prompts/routing/memory layout behind the measurement gate.
**Deliverables:** `omni/evolution/{engine,executor}.py`, `/evolution/*` + `/evolution/apply|revert|mutations|targets` API, tests.
**Status:** ✅ complete — **M7b (mutation executor)** implemented: only **adopted** proposals may mutate live targets (`routing` / `orchestrator_config` dotted-config writes with before/after capture, `prompt` templates written as new immutable memory versions, `memory_layout` branch creation); every mutation is policy-gated (seed rule `rule_allow_evolution_apply`, default deny) and ledger-recorded; config/prompt mutations are reversible (`revert` restores the before-state as a new memory version).

## M8 — Civilisation Mode (✅ core + deploy + message bus complete)

**Scope:** distributed fleet (leader-elected orchestrator, Postgres memory store, message bus), persistent skill marketplace registry, cross-session knowledge fusion, public observability UI, self-healing infra.
**Deliverables:** `omni/fleet/{node,protocol,storage,bus}.py`, `omni/memory/fusion.py`, `omni/marketplace` share APIs, `deploy/k8s/*` + `deploy/helm/omnimind/*`, `/fleet/*` + `/fleet/bus/*` API, tests.
**Status:** ✅ core complete + tested — deterministic leader election (capacity, then node-id tie-break; term persisted by the winner, consensus view from any node), registry announcements with health, shared FIFO task queue with leasing, `PostgresFleetStorage` (schema init + `FOR UPDATE SKIP LOCKED`); **cross-session knowledge fusion** (`MemoryFuser`: newest/merge strategies, sources never touched); **skill sharing** (export/import manifests between catalogs with duplicate/ordering guards); **k8s manifests** (namespace/config/pvc/deployment with probes/service) + **Helm chart** (`deploy/helm/omnimind`). **Message bus (0.5.0):** `FleetBus` — `InMemoryBus` (default, zero-dependency, NATS-style `*`/`>` wildcards) and `NatsBus` (real NATS server via `nats-py`, auto-selected when `NATS_URL` is reachable, graceful fallback otherwise); `FleetNode` publishes `announce` / `leader.elected` / `queue.enqueued` / `queue.leased` events in real time — the feed a WebSocket observability stream will subscribe to next. Remaining (non-blocking): the WebSocket stream itself.

## Milestone gates (every M)

1. All tests for the milestone pass in CI.
2. No placeholder stubs inside claimed-complete modules.
3. API endpoints exercised with a live request.
4. Docs updated (README matrix + this roadmap).
5. Deterministic shutdown — no hidden background writes.
