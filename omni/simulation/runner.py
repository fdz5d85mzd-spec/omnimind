"""Dry-run execution sandbox (M5b): run a plan with zero real side effects.

Structural guarantees — the sandbox never relies on the plan model
"choosing" to behave:

* The default effect backend, ``NoopEffectBackend``, records every effect
  and discards it. It has no access to the filesystem, network, database,
  or processes — applying an effect is a list append, structurally.
* Effects are validated against an explicit allow-list; an unknown effect
  type fails the step and is reported (never executed).
* Hard budgets: ``max_steps``, ``timeout_s`` (a simulated clock) and
  ``max_effects``. Steps beyond a budget are skipped and flagged.
* A step may declare a simulated ``error`` to exercise failure paths;
  its effects are then not applied.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ALLOWED_EFFECTS = frozenset(
    {
        "plan.validate",
        "plan.run",
        "config.write",
        "migration.apply",
        "migration.backfill",
        "index.create",
        "traffic.shift",
        "caches.invalidate",
        "files.write",
        "files.delete_staged",
        "script.echo",
        "infra.apply",
        "infra.simulate",
        "refactor.apply",
        "payment.authorize_stub",
        "email.preview",
        "logs.write",
        "state.write",
        "feature.flag",
    }
)


class NoopEffectBackend:
    """Effect backend that can never touch the real system."""

    def __init__(self) -> None:
        self._log: list[dict[str, Any]] = []

    def apply(self, effect_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._log.append({"effect_type": effect_type, "payload": payload or {}})
        return {"applied": True, "dry_run": True, "effect_type": effect_type}

    @property
    def log(self) -> list[dict[str, Any]]:
        return list(self._log)


@dataclass
class DryRunStepResult:
    step: int
    name: str
    status: str  # ok | failed | skipped
    effects_applied: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "name": self.name,
            "status": self.status,
            "effects_applied": list(self.effects_applied),
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "reason": self.reason,
        }


@dataclass
class DryRunResult:
    run_id: str
    domain: str
    verdict: str  # clean | has_failures | budget_exceeded
    steps_total: int
    steps_ok: int
    steps_failed: int
    steps_skipped: int
    effects_total: int
    rejected_effects: list[str] = field(default_factory=list)
    timeout_hit: bool = False
    budget_reasons: list[str] = field(default_factory=list)
    simulated_duration_ms: float = 0.0
    steps: list[DryRunStepResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "domain": self.domain,
            "verdict": self.verdict,
            "steps_total": self.steps_total,
            "steps_ok": self.steps_ok,
            "steps_failed": self.steps_failed,
            "steps_skipped": self.steps_skipped,
            "effects_total": self.effects_total,
            "rejected_effects": list(self.rejected_effects),
            "timeout_hit": self.timeout_hit,
            "budget_reasons": list(self.budget_reasons),
            "simulated_duration_ms": round(self.simulated_duration_ms, 2),
            "steps": [s.to_dict() for s in self.steps],
        }


class DryRunExecutor:
    def __init__(
        self,
        backend: NoopEffectBackend | None = None,
        allowed_effects: set[str] | None = None,
        max_steps: int = 50,
        timeout_s: float = 30.0,
        max_effects: int = 250,
    ) -> None:
        self.backend = backend if backend is not None else NoopEffectBackend()
        self.allowed_effects = (
            set(allowed_effects) if allowed_effects is not None else set(DEFAULT_ALLOWED_EFFECTS)
        )
        self.max_steps = max_steps
        self.timeout_s = timeout_s
        self.max_effects = max_effects
        self._lock = threading.RLock()

    def run(self, plan: dict[str, Any], domain: str = "generic") -> DryRunResult:
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("plan must contain a non-empty 'steps' list")

        with self._lock:
            clock = 0.0
            effects_total = 0
            rejected: list[str] = []
            budget_reasons: list[str] = []
            timeout_hit = False
            budget_exceeded = False
            results: list[DryRunStepResult] = []
            budget_ms = self.timeout_s * 1000.0  # duration_ms are simulated ms

            def skip(idx: int, step: dict, reason: str) -> DryRunStepResult:
                return DryRunStepResult(
                    step=idx,
                    name=str(step.get("name", f"step-{idx + 1}")),
                    status="skipped",
                    reason=reason,
                )

            for idx, step in enumerate(steps):
                if idx >= self.max_steps:
                    budget_exceeded = True
                    budget_reasons.append(f"step limit ({self.max_steps}) exceeded")
                    results.append(skip(idx, step, "step limit exceeded"))
                    continue

                duration = float(step.get("duration_ms", 0) or 0)
                if clock + duration > budget_ms:
                    timeout_hit = True
                    budget_reasons.append(
                        f"simulated timeout at {clock:.0f}ms (budget {budget_ms:.0f}ms)"
                    )
                    results.append(skip(idx, step, "timeout"))
                    break
                clock += duration

                if step.get("error"):
                    results.append(
                        DryRunStepResult(
                            step=idx,
                            name=str(step.get("name", f"step-{idx + 1}")),
                            status="failed",
                            duration_ms=duration,
                            error=str(step["error"]),
                        )
                    )
                    continue

                effects = list(step.get("effects", []))
                bad = sorted({e for e in effects if e not in self.allowed_effects})
                if bad:
                    rejected.extend(bad)
                    results.append(
                        DryRunStepResult(
                            step=idx,
                            name=str(step.get("name", f"step-{idx + 1}")),
                            status="failed",
                            duration_ms=duration,
                            error=f"effect type(s) not allowed: {', '.join(bad)}",
                        )
                    )
                    continue

                applied: list[str] = []
                abort = False
                for effect in effects:
                    if effects_total >= self.max_effects:
                        budget_exceeded = True
                        budget_reasons.append(f"effect budget ({self.max_effects}) exceeded")
                        results.append(skip(idx, step, "effect budget exceeded"))
                        abort = True
                        break
                    self.backend.apply(effect, {"step": idx, "plan": plan.get("name", "")})
                    applied.append(effect)
                    effects_total += 1
                if abort:
                    for j in range(idx + 1, len(steps)):
                        results.append(skip(j, steps[j], "effect budget exceeded"))
                    break

                results.append(
                    DryRunStepResult(
                        step=idx,
                        name=str(step.get("name", f"step-{idx + 1}")),
                        status="ok",
                        effects_applied=applied,
                        duration_ms=duration,
                    )
                )

            failed = sum(1 for r in results if r.status == "failed")
            skipped = sum(1 for r in results if r.status == "skipped")
            ok = sum(1 for r in results if r.status == "ok")
            if failed > 0 or rejected:
                verdict = "has_failures"
            elif budget_exceeded or timeout_hit:
                verdict = "budget_exceeded"
            else:
                verdict = "clean"

            return DryRunResult(
                run_id=f"dryrun_{uuid.uuid4().hex[:8]}",
                domain=domain,
                verdict=verdict,
                steps_total=len(steps),
                steps_ok=ok,
                steps_failed=failed,
                steps_skipped=skipped,
                effects_total=effects_total,
                rejected_effects=rejected,
                timeout_hit=timeout_hit,
                budget_reasons=budget_reasons,
                simulated_duration_ms=clock,
                steps=results,
            )
