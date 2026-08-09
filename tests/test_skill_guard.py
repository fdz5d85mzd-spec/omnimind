"""Skill permission guard (M4b) tests."""

from omni.contracts.policy import Principal
from omni.contracts.skill import SkillInterface, SkillKind
from omni.marketplace.catalog import SkillCatalog
from omni.marketplace.security import SkillPermissionGuard
from omni.policy.engine import PolicyEngine, make_seed_rules


def _skill_with_permissions(catalog: SkillCatalog, permissions: list[str]):
    return catalog.publish(
        "fs-access",
        "file ops",
        SkillKind.OFFICIAL,
        "core",
        "1.0.0",
        interface=SkillInterface(permissions=permissions),
    )


def test_declared_permissions_checked_against_grants():
    catalog = SkillCatalog()
    skill = _skill_with_permissions(catalog, ["files.write", "files.read"])
    guard = SkillPermissionGuard()

    ok = guard.check_grants(["files.write", "files.read"], skill.latest().interface.permissions)
    assert ok["allowed"] is True and ok["missing"] == []

    denied = guard.check_grants(["files.read"], skill.latest().interface.permissions)
    assert denied["allowed"] is False
    assert denied["missing"] == ["files.write"]


def test_authorize_with_policy_engine():
    catalog = SkillCatalog()
    skill = _skill_with_permissions(catalog, ["files.write"])
    guard = SkillPermissionGuard(policy=PolicyEngine(make_seed_rules()))

    op = guard.authorize(
        Principal(id="ops", roles=["operator"]), "invoke", skill, agent_grants=["files.write"]
    )
    assert op["allowed"] is True

    guest = guard.authorize(
        Principal(id="guest-user", roles=["guest"]), "invoke", skill, agent_grants=["files.write"]
    )
    assert guest["allowed"] is False  # central policy default-deny for guests


def test_authorize_no_policy_no_grants_denies():
    catalog = SkillCatalog()
    skill = _skill_with_permissions(catalog, ["files.write"])
    guard = SkillPermissionGuard()
    decision = guard.authorize(Principal(id="u", roles=["operator"]), "invoke", skill)
    assert decision["allowed"] is False
    assert "no policy engine" in decision["reason"]
