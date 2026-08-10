# Changelog

## [0.8.0] — 2026-08-10

### Added — Public agent runner (`POST /agent/run`)
- `omni/agents/llm.py` — provider-agnostic LLM call (OpenAI or Anthropic,
  via `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, stdlib `urllib` only, no new
  runtime dependency). No key configured raises `LLMNotConfigured` — the
  caller sees a real "not configured" state, never a fabricated answer.
- `omni/agents/runner.py` — `AgentRunner`: the "ask anything" entry point.
  Every call runs through the real Policy Engine (new seed rule
  `rule_allow_web_agent_run` for a `web-user` role), records the prompt
  and answer as real Versioned Memory entries, registers a real
  Meta-Orchestrator agent + task, and publishes every stage
  (`started` / `policy_evaluated` / `memory_stored` / `task_assigned` /
  `thinking` / `completed` or `failed`) to the fleet bus — visible live
  over `/twin/stream` to any connected UI.
- API: `POST /agent/run` (distinct from `/agents/*`, the orchestrator's
  internal fleet registry).
- CORS enabled (`allow_origins=["*"]`) so a separately-hosted frontend
  (the public chat UI) can call this API cross-origin.
- Tests: `tests/test_llm.py` (6), `tests/test_agent_runner.py` (5),
  `tests/test_api_agent_run.py` (4) — suite grows 143 → 158 tests.

## [0.7.0] — 2026-08-10

### Added — Live dashboard UI
- `omni/api/static/index.html` — a single-file, dependency-free dashboard
  (vanilla JS, no build step) served at `GET /dashboard`. Connects to
  `/twin/stream`, renders live stat tiles (agents, tasks, cost, errors),
  an agents table, and a scrolling live fleet-event log; auto-reconnects
  on drop.
- Mounted via `StaticFiles(html=True)` in `omni/api/main.py`.

### Fixed
- **Packaging bug that would have shipped `/dashboard` broken in
  production**: `pip install .` (what the Dockerfile runs) silently drops
  non-`.py` files from a package unless declared as package data —
  `omni/api/static/index.html` was missing from a real installed copy
  even though it worked from the source checkout (where tests run from).
  Verified by building into a clean venv before and after the fix.
  Added `[tool.setuptools.package-data]` (`omni = ["api/static/*"]`) to
  `pyproject.toml`.
- Tests: `tests/test_dashboard.py` (4), including one that guards the
  packaging config directly so this class of bug can't silently return.
  Suite grows 139 → 143 tests.

## [0.6.0] — 2026-08-10

### Added — Live Digital Twin WebSocket stream
- `omni/twin/stream.py` — `TwinBroadcaster`: process-local fan-out, one
  sink per connected client; a sink that raises (dead connection) is
  pruned automatically without breaking delivery to the rest.
- `GET /twin/stream` (WebSocket) — sends an initial full `twin.snapshot()`,
  then every fleet bus event (`announce` / `leader.elected` /
  `queue.enqueued` / `queue.leased`) live as it happens, bridged from the
  synchronous fleet bus callback onto the connection's asyncio loop via
  `call_soon_threadsafe`; falls back to a snapshot heartbeat every 2s when
  the fleet is quiet. `GET /twin/stream/subscribers` reports the open
  connection count.
- No new runtime dependency: `uvicorn[standard]` (already pinned) bundles
  the `websockets` package needed to serve real WebSocket connections.
- Tests: `tests/test_twin_stream.py` (8) — `TwinBroadcaster` fan-out/prune
  semantics, plus end-to-end WebSocket tests via FastAPI's `TestClient`
  (initial snapshot, live fleet-event forwarding, subscriber-count
  accuracy). Suite grows 131 → 139 tests.

## [0.5.0] — 2026-08-10

### Added — Fleet message bus (real-time pub/sub)
- `omni/fleet/bus.py` — `FleetBus` interface, `InMemoryBus` (synchronous,
  zero-dependency, NATS-style `*`/`>` subject wildcards, default backend),
  `NatsBus` (real NATS server via `nats-py`, imported lazily; bridges the
  async client onto a background event-loop thread so the rest of the
  platform's synchronous API is unchanged).
- `FleetNode` now publishes real-time events alongside the existing
  polling storage: `fleet.<node_id>.announce`, `fleet.leader.elected`
  (winner only), `fleet.queue.enqueued`, `fleet.queue.leased`. The `bus`
  constructor arg is optional — polling-only usage is unaffected.
- API: `GET /fleet/bus/status` (backend in use + fallback reason), `GET
  /fleet/bus/events` (recent published events, in-memory backend only).
- The control plane picks `NatsBus` automatically when `NATS_URL` is set
  and reachable; an unreachable/misconfigured NATS server falls back to
  `InMemoryBus` instead of crashing startup.
- Tests: `tests/test_fleet_bus.py` (13), `tests/test_api_fleet_bus.py` (3)
  — suite grows 115 → 131 tests. `NatsBus` itself is not unit-tested (needs
  a live NATS server) — same policy as `PostgresFleetStorage`.

## [0.4.0] — 2026-08-09

### Added — M5b: sandboxed dry-run execution + ML failure prediction
- `omni/simulation/runner.py` — `DryRunExecutor` + `NoopEffectBackend`: executes a
  plan step-by-step with **zero real side effects**, enforced structurally
  (mock effect backend with no system access, explicit effect allow-list,
  hard budgets for steps / simulated time / effects; skip + flag beyond
  budgets; simulated failures per step).
- `omni/simulation/predictor.py` — `FailurePredictor`: trains on execution
  traces with **scikit-learn** (RandomForest) or a **pure-Python logistic
  fallback** (auto-detected; degrades gracefully); `generate_synthetic_traces`
  seeds training data; `save`/`load` persistence; predicts failure
  probability, risk level, and top failure modes per plan.
- API: `/simulation/dryrun`, `/simulation/predict`,
  `/simulation/predictor/status`, `/simulation/predictor/train`.
- CLI: `omnimind dryrun`, `omnimind predict` (inline `--plan` or `--plan-file`).
- SDK: `OmniClient.dry_run`, `predict_failure`, `predictor_status`,
  `train_predictor`.
- Tests: `test_dryrun.py` (8), `test_predictor.py` (7), `test_api_m5b.py` (5)
  — suite grows 94 → 114 tests.

### Changed
- Version bumped to 0.4.0 (`omni/__init__.py`, `pyproject.toml`, fleet
  protocol/node defaults, k8s/Helm image tags).
- `dev` extras gain `scikit-learn>=1.3` (optional ML dependency).

## [0.3.0] — 2026-08-09

### Added — M7b: Self Evolution mutation executor
- `omni/evolution/executor.py` — `EvolutionExecutor` applies **adopted**
  proposals as live mutations: `routing` / `orchestrator_config`
  (dotted-config writes with before/after capture), `prompt` (new immutable
  memory version per task type), `memory_layout` (branch creation).
- Every mutation is **policy-gated** (new seed rule `rule_allow_evolution_apply`;
  default deny) and **ledger-recorded** (`subsystem="evolution"`); config and
  prompt mutations are **reversible** (`revert` restores the before-state as a
  new version; memory branches are retained by immutability).
- API: `/evolution/apply`, `/evolution/revert`, `/evolution/mutations`,
  `/evolution/targets`. Tests: `tests/test_evolution_executor.py` (6 tests).

### Added — M8: skill sharing, knowledge fusion, deployment assets
- `omni/marketplace/catalog.py` — `export_manifest` / `import_manifest`:
  share skills between catalogs with duplicate-id and version-ordering guards.
  Tests: `tests/test_skill_share.py` (4 tests).
- `omni/memory/fusion.py` — `MemoryFuser` (cross-session knowledge fusion):
  `newest` and `merge` (recursive deep-merge) strategies consolidate branched
  memories back into a target branch; source branches are never touched;
  unchanged keys are not rewritten. Tests: `tests/test_memory_fusion.py`
  (4 tests).
- `deploy/k8s/` — namespace, configmap, PVC, deployment (with readiness/
  liveness probes, resource requests/limits), service, `kustomization.yaml`.
- `deploy/helm/omnimind/` — Chart.yaml, values.yaml, deployment/service/PVC
  templates.

### Changed
- Version bumped to 0.3.0 (`omni/__init__.py`, `pyproject.toml`).
- `EvolutionEngine` gains a public `get()` (used by the executor).
- Test suite grew from 79 → 94 tests (all passing).

## [0.2.0] — 2026-08-08

### Added — M4b: Dynamic Skill Marketplace extensions
- `omni/marketplace/remote.py` — `RemoteSkillRegistry`: syncs versioned skill
  manifests from arbitrary registry URLs; provenance stamped (`source_url`,
  `last_synced`); older versions skipped; no-op on re-sync.
- `omni/marketplace/security.py` — `SkillPermissionGuard`: enforces a skill's
  declared `interface.permissions` against agent grant sets, with central
  Policy Engine fallback (default deny).
- API: `/marketplace/remote/register`, `/marketplace/remote/sync`,
  `/marketplace/remote/sources`, `/marketplace/authorize`.

### Added — M6b: persistent metrics store + replayable audit ledger
- `omni/learning/store.py` — `EvaluationStore` (SQLite): evaluations survive
  process restarts; pipeline reads history/trends from the store when wired.
- `omni/learning/aggregate.py` — shared aggregation/trend helpers used by both
  the in-memory pipeline and the persistent store.
- `omni/audit/ledger.py` — `ReplayLedger`: append-only, queryable record of
  every system decision; `PolicyEngine` now records every decision into it.
- API: `/twin/replay`, `/audit/replay`.

### Added — M8: distributed fleet
- `omni/fleet/protocol.py` — `NodeAnnouncement`, `NodeHealth`, `WorkloadStats`,
  `LeaderInfo`, node id generation.
- `omni/fleet/node.py` — `FleetNode`: announce into registry, deterministic
  leader election (capacity → node-id tie-break), workload stats from a local
  orchestrator, enqueue/adopt shared tasks.
- `omni/fleet/storage.py` — `FleetStorage` interface, `InMemoryFleetStorage`
  (tests), `PostgresFleetStorage` (production: schema init, atomic leasing via
  `FOR UPDATE SKIP LOCKED`).
- API: `/fleet/status`, `/fleet/announce`, `/fleet/elect`, `/fleet/enqueue`,
  `/fleet/adopt`, `/fleet/leader`. CLI: `omnimind fleet`.

### Added — Client SDK + CLI
- `omni/sdk.py` — `OmniClient` with injectable transport (HTTP default):
  policy, memory, tasks, skills, simulation, twin, replay.
- `omni/cli.py` — `omnimind` CLI: `version`, `demo` (in-process platform run),
  `policy`, `memory`, `twin`, `simulate`, `fleet`.

### Changed
- Version bumped to 0.2.0.
- `PolicyDecision` carries `principal_id`; the Policy Engine records every
  decision into an optional `ReplayLedger`.
- `MetaOrchestrator` exposes `arrival_rate()` and `total_cost()` for fleet
  workload stats.
- Seed rules gain `rule_allow_skill_ops` (operator/admin may operate skills).
- Test suite grew from 52 → 79 tests.

## [0.1.0] — 2026-08-07
Initial platform: contracts, Policy Engine, Versioned Memory, Meta-Orchestrator,
Skill Marketplace core, Simulation Sandbox, Learning Pipeline, Digital Twin,
Self Evolution Engine, FastAPI control plane, 52 tests.
