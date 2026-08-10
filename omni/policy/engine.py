"""Policy Engine — RBAC + ABAC evaluation, default-deny, risk gating,
approval chains, rolling-window limits and emergency lockdown.

Design invariants
-----------------
* Default deny: an action matched by no ALLOW rule is denied.
* Deny-before-allow: any matching DENY rule wins over ALLOW rules.
* Every decision is appended to an immutable decision log.
* Usage (cost / calls) is charged on a rolling window per principal,
  group, or globally, only for allowed actions.
"""

from __future__ import annotations

import itertools
import threading
import time
from datetime import datetime, timezone
from typing import Any

from omni.contracts.policy import (
    ABACCondition,
    ApprovalStatus,
    LimitPolicy,
    LimitStatus,
    PolicyDecision,
    PolicyRule,
    Principal,
    Resource,
    RiskLevel,
    risk_at_least,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_path(obj: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _eval_condition(cond: ABACCondition, principal: Principal, resource: Resource) -> bool:
    if cond.field.startswith("principal."):
        actual = _get_path(
            {"id": principal.id, "roles": principal.roles, "groups": principal.groups, "attributes": principal.attributes},
            cond.field.removeprefix("principal."),
        )
    elif cond.field.startswith("resource."):
        # resource.risk_level lives inside attributes
        res_dict = {"type": resource.type, **resource.attributes}
        actual = _get_path(res_dict, cond.field.removeprefix("resource."))
    else:
        return False

    want = cond.value
    op = cond.op
    if op == "eq":
        return actual == want
    if op == "ne":
        return actual != want
    c = _compare(actual, want)
    if c is None:
        return False
    if op == "gt":
        return c > 0
    if op == "gte":
        return c >= 0
    if op == "lt":
        return c < 0
    if op == "lte":
        return c <= 0
    if op == "in":
        return actual in (want or [])
    if op == "contains":
        return any(item in (actual or []) for item in (want or []))
    return False


def _compare(a: Any, b: Any) -> int | None:
    """-1/0/1 comparator; risk-level strings compare by severity order.
    Returns None when the values are not comparable."""
    if isinstance(a, str) and isinstance(b, str):
        ia, ib = _risk_index(a), _risk_index(b)
        if ia >= 0 and ib >= 0:
            return (ia > ib) - (ia < ib)
    try:
        return (a > b) - (a < b)
    except TypeError:
        return None


def _risk_index(v: str) -> int:
    try:
        return [r.value for r in RiskLevel].index(v)
    except ValueError:
        return -1


def _action_matches(pattern: str, action: str) -> bool:
    """Match an action against a rule pattern with `*` wildcards.

    Patterns: exact ('deploy'), single-segment wildcard ('agent.*' matches
    'agent.spawn' and 'agent.terminate'), or global wildcard ('*' matches
    anything).
    """
    if pattern == "*":
        return True
    if "*" not in pattern:
        return pattern == action
    prefix = pattern[:-1]  # e.g. 'agent.'
    return action.startswith(prefix)


class PolicyEngine:
    """Thread-safe decision engine with an append-only decision log."""

    def __init__(self, seed_rules: list[PolicyRule] | None = None, ledger=None) -> None:
        self._rules: list[PolicyRule] = []
        self._limits: list[LimitPolicy] = []
        self._lock = threading.RLock()
        self._lockdown = False
        self._decisions: list[PolicyDecision] = []  # append-only audit log
        self._pendings: dict[str, PolicyDecision] = {}  # approval chains
        self._usage: list[tuple[float, str, str, float, int]] = []  # (ts, scope, scope_value, cost, calls)
        self._ledger = ledger  # optional ReplayLedger — every decision becomes replayable
        for rule in seed_rules or []:
            self.add_rule(rule)

    # ------------------------------------------------------------- setup
    def add_rule(self, rule: PolicyRule) -> PolicyRule:
        with self._lock:
            self._rules.append(rule)
            return rule

    def add_limit(self, limit: LimitPolicy) -> LimitPolicy:
        with self._lock:
            self._limits.append(limit)
            return limit

    def set_lockdown(self, enabled: bool) -> bool:
        with self._lock:
            self._lockdown = enabled
            return enabled

    @property
    def lockdown(self) -> bool:
        return self._lockdown

    # ------------------------------------------------------------- evaluate
    def evaluate(
        self,
        principal: Principal,
        action: str,
        resource: Resource | None = None,
        cost: float = 0.0,
        calls: int = 1,
    ) -> PolicyDecision:
        resource = resource or Resource()
        with self._lock:
            # Emergency lockdown: only system admins act.
            if self._lockdown and "system.admin" not in principal.roles:
                return self._log(
                    PolicyDecision(
                        allowed=False,
                        reason=f"emergency lockdown active; action '{action}' denied for principal '{principal.id}'",
                    )
                )

            matching = sorted(
                (r for r in self._rules if self._rule_matches(r, principal, action, resource)),
                key=lambda r: r.priority,
                reverse=True,
            )

            # Deny-before-allow.
            for rule in matching:
                if rule.effect == "DENY":
                    return self._log(
                        PolicyDecision(
                            allowed=False,
                            matched_rule=rule.id,
                            reason=f"denied by rule '{rule.id}' ({rule.action})",
                            risk_level=resource.risk(),
                            limit_status=self._check_limits(principal, cost, calls),
                        )
                    )

            allow = next((r for r in matching if r.effect == "ALLOW"), None)
            if allow is None:
                return self._log(
                    PolicyDecision(
                        allowed=False,
                        reason=f"no policy matches action '{action}' for principal '{principal.id}' (default deny)",
                        risk_level=resource.risk(),
                        limit_status=self._check_limits(principal, cost, calls),
                    )
                )

            # ABAC risk gate: resource risk must not exceed rule's allowance.
            if allow.risk_level is not None and risk_at_least(resource.risk(), allow.risk_level):
                denied = PolicyDecision(
                    allowed=False,
                    matched_rule=allow.id,
                    reason=(
                        f"risk gate: resource risk '{resource.risk().value}' exceeds "
                        f"allowed '{allow.risk_level.value}' by rule '{allow.id}'"
                    ),
                    principal_id=principal.id,
                    risk_level=resource.risk(),
                    limit_status=self._check_limits(principal, cost, calls),
                )
                return self._log(denied)

            # Rolling-window limits.
            limit_status = self._check_limits(principal, cost, calls)
            if not limit_status.allowed:
                denied = PolicyDecision(
                    allowed=False,
                    matched_rule=allow.id,
                    reason=f"limit exceeded: {', '.join(limit_status.violations)}",
                    risk_level=resource.risk(),
                    limit_status=limit_status,
                )
                return self._log(denied)

            # Approval chain (unless exempt).
            needs_approval = allow.require_approval and "approval.exempt" not in principal.roles
            if needs_approval:
                pending = PolicyDecision(
                    allowed=False,
                    matched_rule=allow.id,
                    reason=f"requires approval by roles {allow.approve_roles} (rule '{allow.id}')",
                    principal_id=principal.id,
                    risk_level=resource.risk(),
                    require_approval=True,
                    approve_roles=list(allow.approve_roles),
                    approval_status=ApprovalStatus.PENDING,
                    limit_status=limit_status,
                )
                self._pendings[pending.decision_id] = pending
                return self._log(pending)

            self._record_usage(principal, cost, calls)
            return self._log(
                PolicyDecision(
                    allowed=True,
                    matched_rule=allow.id,
                    reason=f"allowed by rule '{allow.id}'",
                    principal_id=principal.id,
                    risk_level=resource.risk(),
                    limit_status=limit_status,
                )
            )

    # ------------------------------------------------------------- approvals
    def approve(self, decision_id: str, approver_role: str) -> PolicyDecision:
        with self._lock:
            pending = self._pendings.get(decision_id)
            if pending is None:
                raise KeyError(f"no pending approval '{decision_id}'")
            if approver_role not in pending.approve_roles:
                rejected = pending.model_copy(
                    update={
                        "approval_status": ApprovalStatus.REJECTED,
                        "reason": f"approved denied: '{approver_role}' lacks an approval role",
                    }
                )
                self._log(rejected)
                return rejected
            approved = pending.model_copy(
                update={
                    "allowed": True,
                    "approval_status": ApprovalStatus.APPROVED,
                    "reason": f"approved by role '{approver_role}'",
                }
            )
            self._pendings.pop(decision_id, None)
            self._log(approved)
            return approved

    def pending_approvals(self) -> list[PolicyDecision]:
        with self._lock:
            return [d for d in self._pendings.values()]

    # ------------------------------------------------------------- audit
    def decision_log(self, limit: int = 200) -> list[PolicyDecision]:
        with self._lock:
            return self._decisions[-limit:]

    # ------------------------------------------------------------- internals
    def _rule_matches(self, rule: PolicyRule, principal: Principal, action: str, resource: Resource) -> bool:
        if not _action_matches(rule.action, action):
            return False
        if rule.roles and not set(rule.roles) & set(principal.roles):
            return False
        if rule.groups and not set(rule.groups) & set(principal.groups):
            return False
        return all(_eval_condition(c, principal, resource) for c in rule.conditions)

    def _check_limits(self, principal: Principal, cost: float, calls: int) -> LimitStatus:
        now = time.time()
        status = LimitStatus(allowed=True)
        scopes: list[tuple[str, str]] = [
            ("GLOBAL", "*"),
            *[(p, g) for g in principal.groups for p in ("GROUP", g)],
            ("PRINCIPAL", principal.id),
        ]
        windows = {
            (scope, value, l.id): l
            for l in self._limits
            for scope, value in scopes
            if l.scope == scope and l.scope_value in ("*", value)
        }
        for (scope, value, _), lim in windows.items():
            recent = [
                (ts, c, n)
                for ts, s, sv, c, n in self._usage
                if s == scope and sv == value and (now - ts) <= lim.window_seconds
            ]
            used_cost = sum(c for _, c, _ in recent)
            used_calls = sum(n for _, _, n in recent)
            if lim.max_cost is not None and used_cost + cost > lim.max_cost:
                status.allowed = False
                status.violations.append(
                    f"cost limit '{lim.id}' ({scope}:{value}): {used_cost + cost:.2f} > {lim.max_cost}"
                )
            if lim.max_calls is not None and used_calls + calls > lim.max_calls:
                status.allowed = False
                status.violations.append(
                    f"call limit '{lim.id}' ({scope}:{value}): {used_calls + calls} > {lim.max_calls}"
                )
            status.current_cost = round(max(status.current_cost, used_cost), 4)
            status.current_calls = max(status.current_calls, used_calls)
        return status

    def _record_usage(self, principal: Principal, cost: float, calls: int) -> None:
        if cost <= 0 and calls <= 0:
            return
        now = time.time()
        for group in principal.groups:
            self._usage.append((now, "GROUP", group, cost, calls))
        self._usage.append((now, "PRINCIPAL", principal.id, cost, calls))
        self._usage.append((now, "GLOBAL", "*", cost, calls))

    def _log(self, decision: PolicyDecision) -> PolicyDecision:
        self._decisions.append(decision)
        if self._ledger is not None:
            self._ledger.record(
                subsystem="policy",
                subject=decision.principal_id or "policy",
                event=f"decision.{'allow' if decision.allowed else 'deny'}",
                payload=decision.model_dump(mode="json"),
                decision_id=decision.decision_id,
            )
        return decision


def make_seed_rules() -> list[PolicyRule]:
    """Convenience rules used by the default control plane."""
    return [
        PolicyRule(
            id="rule_allow_agent_ops",
            action="agent.*",
            effect="ALLOW",
            roles=["operator", "system.admin"],
            conditions=[ABACCondition(field="resource.risk_level", op="lte", value="medium")],
            priority=100,
            risk_level=None,
        ),
        PolicyRule(
            id="rule_allow_web_agent_run",
            action="agent.run",
            effect="ALLOW",
            roles=["web-user", "operator", "system.admin"],
            conditions=[ABACCondition(field="resource.risk_level", op="lte", value="low")],
            priority=95,
        ),
        PolicyRule(
            id="rule_allow_read",
            action="memory.read",
            effect="ALLOW",
            roles=["*" if False else "reader", "operator", "system.admin"],
            priority=90,
        ),
        PolicyRule(
            id="rule_write_operator",
            action="memory.write",
            effect="ALLOW",
            roles=["operator", "system.admin"],
            priority=80,
        ),
        PolicyRule(
            id="rule_allow_skill_ops",
            action="skill.*",
            effect="ALLOW",
            roles=["operator", "system.admin"],
            priority=70,
        ),
        PolicyRule(
            id="rule_allow_evolution_apply",
            action="evolution.*",
            effect="ALLOW",
            roles=["system.admin"],
            priority=75,
        ),
        PolicyRule(
            id="rule_deploy_admin",
            action="deploy",
            effect="ALLOW",
            roles=["release-engineer", "system.admin"],
            priority=60,
            require_approval=True,
            approve_roles=["release-manager", "system.admin"],
        ),
        PolicyRule(
            id="rule_deny_guests_write",
            action="*",
            effect="DENY",
            roles=["guest"],
            priority=200,
        ),
        PolicyRule(
            id="rule_deny_payment_guest",
            action="payment.*",
            effect="DENY",
            roles=["guest", "reader"],
            priority=150,
        ),
    ]
