"""Simulation Sandbox: risk estimation, failure/side-effect prediction,
rollback plans, alternatives, confidence score, approval gating.

Design
------
* Each domain (deploy, db_migration, file_delete, script_run, infra_change,
  refactor, payment, email_campaign) carries a parameterized risk model:
  base risk + adjustments (blast radius, reversibility, data sensitivity).
* Predicted failures and side effects come from static rule tables per domain.
* `confidence` = 1 - risk_score, clamped to [0, 1].
* If confidence < policy threshold OR the action is inherently irreversible
  (payment, hard delete), the plan pauses and requests human approval.

M5b additions (implemented):
* `runner.py` — `DryRunExecutor` + `NoopEffectBackend`: structural dry-run of a
  plan with zero real side effects (mock backend with no system access,
  explicit effect allow-list, hard step/time/effect budgets, simulated failures).
* `predictor.py` — `FailurePredictor`: trains on execution traces with
  scikit-learn (RandomForest) or a pure-Python logistic fallback; failure
  probability + risk level + top failure modes per plan.
The heuristic risk model above remains the pre-flight estimate; prediction
improves as real execution traces are fed into the ML pipeline.
"""

from __future__ import annotations

import threading
from typing import Any

from omni.contracts.policy import RiskLevel
from omni.contracts.simulation import SimulationResult

SIMULATION_DOMAINS = {
    "deploy": {
        "base_risk": 0.25,
        "blast_radius": 1.0,  # multiplies risk with size
        "failures": [
            "config drift between environments",
            "container image pull failure",
            "partial rollout exposing incompatible schema",
        ],
        "side_effects": ["traffic shifted to new pods", "caches invalidated", "observability dashboards refresh"],
        "rollback": ["scale previous release back up", "re-point ingress to previous canary", "restore manifest snapshot"],
        "always_approve": False,
    },
    "db_migration": {
        "base_risk": 0.45,
        "blast_radius": 1.4,
        "failures": ["locking contention on the target table", "row count mismatch after backfill", "index build timeout"],
        "side_effects": ["schema lock held", "WAL growth", "replication lag"],
        "rollback": ["apply compensating migration", "restore from pre-migration backup", "feature-flag off the new path"],
        "always_approve": False,
    },
    "file_delete": {
        "base_risk": 0.2,
        "blast_radius": 0.6,
        "failures": ["undeleted references still pointing at the files", "backup lag means no viable copy"],
        "side_effects": ["disk space freed", "search index entries orphaned"],
        "rollback": ["restore from trash/backup", "re-link references from manifest"],
        "always_approve": True,
    },
    "script_run": {
        "base_risk": 0.15,
        "blast_radius": 0.5,
        "failures": ["unhandled exception mid-run leaves partial state", "environment variables missing"],
        "side_effects": ["logs written", "temp files created", "shared cache mutated"],
        "rollback": ["re-run from checkpoint", "delete produced artifacts"],
        "always_approve": False,
    },
    "infra_change": {
        "base_risk": 0.35,
        "blast_radius": 1.2,
        "failures": ["capacity re-sizing drops below burst demand", "firewall rule typo interrupts ingress"],
        "side_effects": ["instances recycled", "DNS propagation", "public IP changes"],
        "rollback": ["apply previous terraform state", "revert security-group diff"],
        "always_approve": False,
    },
    "refactor": {
        "base_risk": 0.2,
        "blast_radius": 0.7,
        "failures": ["behavioral drift in edge cases", "public API signature breakage"],
        "side_effects": ["coverage report changes", "dependency graph rewritten"],
        "rollback": ["git revert of the refactor commit", "restore previous package artifact"],
        "always_approve": False,
    },
    "payment": {
        "base_risk": 0.7,
        "blast_radius": 1.6,
        "failures": ["double charge on idempotency miss", "gateway timeout after authorization", "currency conversion mismatch"],
        "side_effects": ["ledger entries created", "invoice emailed", "bank reconciliation affected"],
        "rollback": ["issue refund transaction", "void authorization", "flag for manual reconciliation"],
        "always_approve": True,
    },
    "email_campaign": {
        "base_risk": 0.3,
        "blast_radius": 1.0,
        "failures": ["template variable renders empty", "segment filter over-includes", "deliverability warm-up exceeded"],
        "side_effects": ["campaign metric dashboards update", "unsubscribe list changes"],
        "rollback": ["pause send pipeline", "send corrective email", "adjust segment definition"],
        "always_approve": False,
    },
}


class SimulationSandbox:
    def __init__(self, confidence_threshold: float = 0.7, domains: dict[str, Any] | None = None) -> None:
        self._lock = threading.RLock()
        self.confidence_threshold = confidence_threshold
        self._domains = domains or SIMULATION_DOMAINS
        self._results: list[SimulationResult] = []

    def simulate(
        self,
        action: str,
        domain: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> SimulationResult:
        with self._lock:
            params = params or {}
            model = self._domains.get(domain)
            if model is None:
                raise KeyError(f"unknown simulation domain '{domain}' (known: {sorted(self._domains)})")

            base = model["base_risk"]
            blast = model["blast_radius"]
            size = float(params.get("size", 1))
            reversible = bool(params.get("reversible", True))
            sensitive = bool(params.get("sensitive_data", False))

            risk = min(0.95, base * blast * (0.6 + 0.4 * size))
            if not reversible:
                risk = min(0.95, risk + 0.15)
            if sensitive:
                risk = min(0.95, risk + 0.1)

            confidence = round(max(0.0, 1.0 - risk), 3)
            if confidence >= 0.85:
                risk_level = RiskLevel.LOW
            elif confidence >= 0.7:
                risk_level = RiskLevel.MEDIUM
            elif confidence >= 0.5:
                risk_level = RiskLevel.HIGH
            else:
                risk_level = RiskLevel.CRITICAL

            needs_approval = model["always_approve"] or confidence < self.confidence_threshold
            result = SimulationResult(
                action=action,
                domain=domain,
                risk_score=round(risk, 3),
                risk_level=risk_level,
                predicted_failures=list(model["failures"]),
                side_effects=list(model["side_effects"]),
                rollback_plan=list(model["rollback"]),
                alternatives=self._alternatives(domain, confidence),
                confidence=confidence,
                recommended_plan="pause_for_approval" if needs_approval else "proceed",
                needs_approval=needs_approval,
                approval_reason=(
                    "payment/file-delete domain requires human approval by policy"
                    if model["always_approve"]
                    else f"confidence {confidence:.2f} below threshold {self.confidence_threshold:.2f}"
                ),
                params=params,
            )
            self._results.append(result)
            return result

    def approve(self, simulation_id: str, approver: str) -> SimulationResult:
        with self._lock:
            result = next((r for r in self._results if r.simulation_id == simulation_id), None)
            if result is None:
                raise KeyError(f"no simulation '{simulation_id}'")
            if not result.needs_approval:
                raise ValueError("simulation did not request approval")
            result.needs_approval = False
            result.approved_by = approver
            result.recommended_plan = "proceed (human approved)"
            return result

    def history(self) -> list[SimulationResult]:
        with self._lock:
            return list(self._results)

    def _alternatives(self, domain: str, confidence: float) -> list[str]:
        safe = [
            "run inside isolated staging environment",
            "execute with feature flag off for 24h observation",
            "limit blast radius to 1% canary first",
        ]
        if domain == "payment":
            safe[1] = "process a $1 micro-transaction end-to-end first"
        if domain == "db_migration":
            safe[2] = "expand-contract: add new column, backfill, then swap read path"
        if domain == "file_delete":
            safe[1] = "move to trash with 30-day retention instead of hard delete"
        return safe
