"""Skill share tests: export / import roundtrip, duplicate & ordering guards."""

import pytest

from omni.contracts.skill import SkillInterface, SkillKind
from omni.marketplace.catalog import SkillCatalog


def _populated_catalog() -> SkillCatalog:
    catalog = SkillCatalog()
    catalog.publish(
        "code-gen", "generate code", SkillKind.COMMUNITY, "dev", "1.0.0",
        interface=SkillInterface(actions=["generate"], permissions=["files.write"]),
        tags=["codegen"],
    )
    catalog.publish(
        "code-gen", "generate code", SkillKind.COMMUNITY, "dev", "1.1.0",
        interface=SkillInterface(actions=["generate", "generate_many"]),
    )
    return catalog


def test_export_import_roundtrip_between_catalogs():
    source = _populated_catalog()
    skill = source.search("code-gen")[0]
    exported = source.export_manifest(skill.id)

    target = SkillCatalog()
    imported = target.import_manifest(exported)
    assert imported.name == "code-gen"
    assert imported.latest_version == "1.1.0"
    assert len(imported.versions) == 2
    assert imported.versions[0].interface.actions == ["generate"]
    assert imported.kind == SkillKind.COMMUNITY
    # independent instances — same content, separate objects
    assert imported is not skill
    assert imported.versions is not skill.versions
    assert source.get(imported.id) is not None  # id is a content identifier, both catalogs hold their own copy


def test_import_rejects_duplicate_id():
    catalog = _populated_catalog()
    skill = catalog.search("code-gen")[0]
    exported = catalog.export_manifest(skill.id)
    with pytest.raises(ValueError):
        catalog.import_manifest(exported)


def test_import_rejects_out_of_order_versions():
    catalog = SkillCatalog()
    data = {
        "name": "bad-skill",
        "kind": "official",
        "author": "x",
        "versions": [
            {"version": "1.0.0", "interface": {}},
            {"version": "0.9.0", "interface": {}},
        ],
        "latest_version": "1.0.0",
    }
    with pytest.raises(ValueError):
        catalog.import_manifest(data)


def test_export_unknown_skill_raises():
    catalog = SkillCatalog()
    with pytest.raises(KeyError):
        catalog.export_manifest("skill_nope")
