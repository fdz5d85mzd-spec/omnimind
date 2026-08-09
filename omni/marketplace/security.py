"""Skill permission guard (M4b): enforce the permissions a skill declares
against the grants an agent holds, with a Policy Engine fallback.

A skill's declared `interface.permissions` (e.g. ["files.write"]) is the
contract: before the skill may be invoked, the calling agent must either hold
those grants (runtime grant set) or the central policy engine must allow the
`skill.<action>` action. Default remains deny.
"""

from __future__ import annotations

from omni.contracts.policy import Principal, Resource
from omni.contracts.skill import SkillManifest
from omni.policy.engine import PolicyEngine


class SkillPermissionGuard:
    def __init__(self, policy: PolicyEngine | None = None, allow_without_policy: bool = False) -> None:
        self._policy = policy
        self._allow_without_policy = allow_without_policy

    # ------------------------------------------------------------ grant checks
    def check_grants(self, agent_grants: list[str], required: list[str]) -> dict:
        """Pure set check: every required permission must be granted."""
        missing = [p for p in required if p not in agent_grants]
        return {"allowed": not missing, "missing": missing}

    # ------------------------------------------------------------ authorize
    def authorize(
        self,
        principal: Principal,
        action: str,
        skill: SkillManifest | None = None,
        agent_grants: list[str] | None = None,
    ) -> dict:
        required: list[str] = []
        if skill is not None and skill.latest() is not None:
            required = skill.latest().interface.permissions

        # 1) declared-permission contract against agent grants
        if agent_grants is not None:
            decision = self.check_grants(agent_grants, required)
            if not decision["allowed"]:
                return {
                    "allowed": False,
                    "reason": "agent lacks required skill permissions",
                    "missing": decision["missing"],
                }

        # 2) central policy fallback (default deny when no rule matches)
        if self._policy is not None:
            decision = self._policy.evaluate(
                principal,
                f"skill.{action}",
                Resource(type="skill", attributes={"skill": skill.name if skill else "unknown"}),
            )
            if not decision.allowed:
                return {"allowed": False, "reason": decision.reason}

        if self._policy is not None or self._allow_without_policy:
            return {"allowed": True, "reason": "authorized"}
        return {"allowed": False, "reason": "no policy engine and no grant set provided"}
