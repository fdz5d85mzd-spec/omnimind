"""Autonomous mutation executor (M7b) — applies adopted evolution proposals.

An adopted proposal becomes a *mutation* against a live target:

* ``routing`` / ``orchestrator_config``  — set dotted config paths (before/after captured)
* ``prompt``                            — write a versioned prompt template (Versioned Memory)
* ``memory_layout``                     — create a memory branch (Versioned Memory)

Guarantees
----------
* A proposal must be **adopted** before it may be applied.
* The central Policy Engine gates every application (default deny).
* Every mutation is recorded in the ReplayLedger and is **reversible**
  (config/prompt restore the before-state as a new memory version; branches
  are immutable, so they are retained but the record is marked non-revertible).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from omni.contracts.memory import BranchRequest
from omni.contracts.policy import Principal, Resource

MUTATION_TARGETS = ("routing", "prompt", "memory_layout", "orchestrator_config")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_dotted(data: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


@dataclass
class MutationRecord:
    mutation_id: str = field(default_factory=lambda: f"mut_{uuid.uuid4().hex[:8]}")
    proposal_id: str = ""
    target: str = ""
    mutation: dict = field(default_factory=dict)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    status: str = "applied"  # applied | reverted
    revertible: bool = True
    note: str = ""
    applied_at: str = field(default_factory=_now_iso)
    reverted_at: str | None = None

    def to_dict(self) -> dict:
        return vars(self)


class EvolutionExecutor:
    def __init__(
        self,
        evolution=None,
        config: dict[str, Any] | None = None,
        memory=None,
        ledger=None,
        policy=None,
        approver_role: str = "system.admin",
    ) -> None:
        self._lock = threading.RLock()
        self._evolution = evolution
        self._config: dict[str, Any] = config if config is not None else {}
        self._memory = memory
        self._ledger = ledger
        self._policy = policy
        self._approver_role = approver_role
        self._records: list[MutationRecord] = []

    # ------------------------------------------------------------ apply
    def apply(self, proposal_id: str, target: str, mutation: dict) -> MutationRecord:
        if target not in MUTATION_TARGETS:
            raise KeyError(f"unknown mutation target '{target}' (known: {MUTATION_TARGETS})")
        if self._evolution is not None:
            proposal = self._evolution.get(proposal_id)
            if proposal.status != "adopted":
                raise ValueError(f"proposal '{proposal_id}' is not adopted (status={proposal.status})")
        if self._policy is not None:
            decision = self._policy.evaluate(
                Principal(id="evolution", roles=[self._approver_role]),
                "evolution.apply",
                Resource(type="evolution", attributes={"target": target}),
            )
            if not decision.allowed:
                raise PermissionError(f"policy denied evolution mutation: {decision.reason}")

        with self._lock:
            record = self._apply_locked(proposal_id, target, mutation)
            self._records.append(record)
        if self._ledger is not None:
            self._ledger.record(
                subsystem="evolution",
                subject=proposal_id,
                event=f"mutation.{record.status}",
                payload={"mutation_id": record.mutation_id, "target": target, "mutation": mutation},
                decision_id=record.mutation_id,
            )
        return record

    def _apply_locked(self, proposal_id: str, target: str, mutation: dict) -> MutationRecord:
        record = MutationRecord(proposal_id=proposal_id, target=target, mutation=mutation)

        if target in ("routing", "orchestrator_config"):
            sets = mutation.get("set", {})
            record.before = {"values": {k: _get_dotted(self._config, k) for k in sets}}
            for k, v in sets.items():
                _set_dotted(self._config, k, v)
            record.after = {"values": {k: _get_dotted(self._config, k) for k in sets}}

        elif target == "prompt":
            prompt = mutation.get("prompt", {})
            task_type = prompt.get("task_type", "generic")
            key = f"prompts.{task_type}"
            if self._memory is not None:
                existing = self._memory.read(key)
                record.before = {"template": existing.value.get("template") if existing else None}
                self._memory.write(
                    key, {"task_type": task_type, "template": prompt.get("template")},
                    agent_id="evolution", reason=f"adopted proposal {proposal_id}",
                )
                record.after = {"template": prompt.get("template")}
            else:
                record.before = {"template": _get_dotted(self._config, key)}
                _set_dotted(self._config, key, prompt.get("template"))
                record.after = {"template": _get_dotted(self._config, key)}

        elif target == "memory_layout":
            req = mutation.get("branch", {})
            if self._memory is None:
                raise ValueError("memory_layout mutation requires a Versioned Memory store")
            entry = self._memory.branch(
                BranchRequest(
                    key=req.get("key", "knowledge"),
                    source_branch=req.get("source_branch", "main"),
                    new_branch=req.get("new_branch", f"exp-{proposal_id[:6]}"),
                    agent_id="evolution",
                    reason=f"adopted proposal {proposal_id}: {req.get('reason', '')}",
                )
            )
            record.before = {"branches": [entry.branch]}
            record.after = {"branches": [entry.branch], "version": entry.version}
            record.note = f"created branch '{entry.branch}' (immutable memory; retained on revert)"
            record.revertible = False

        return record

    # ------------------------------------------------------------ revert
    def revert(self, mutation_id: str) -> MutationRecord:
        with self._lock:
            record = next((r for r in self._records if r.mutation_id == mutation_id), None)
            if record is None:
                raise KeyError(f"no mutation '{mutation_id}'")
            if record.status == "reverted":
                return record
            if not record.revertible:
                record.status = "reverted"
                record.reverted_at = _now_iso()
                return record

            if record.target in ("routing", "orchestrator_config"):
                for k, v in record.before.get("values", {}).items():
                    _set_dotted(self._config, k, v)
            elif record.target == "prompt":
                task_type = record.mutation.get("prompt", {}).get("task_type", "generic")
                key = f"prompts.{task_type}"
                if self._memory is not None:
                    self._memory.write(
                        key,
                        {"task_type": task_type, "template": record.before.get("template")},
                        agent_id="evolution",
                        reason=f"revert mutation {mutation_id}",
                    )
                else:
                    _set_dotted(self._config, key, record.before.get("template"))
            record.status = "reverted"
            record.reverted_at = _now_iso()
            return record

    def records(self) -> list[MutationRecord]:
        with self._lock:
            return list(self._records)
