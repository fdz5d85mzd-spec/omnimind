"""Skill Marketplace tests: publish/upgrade, install/remove, rate,
recommend, search by query and kind."""

import pytest

from omni.contracts.skill import SkillInterface, SkillKind
from omni.marketplace.catalog import SkillCatalog


def test_publish_and_upgrade_versions():
    c = SkillCatalog()
    s = c.publish(
        "summarize", "summarize docs", SkillKind.OFFICIAL, "core", "1.0.0",
        interface=SkillInterface(actions=["summarize"]), tags=["nlp"],
    )
    assert s.latest_version == "1.0.0"
    c.publish("summarize", "summarize docs", SkillKind.OFFICIAL, "core", "1.1.0")
    manifest = c.get(s.id)
    assert manifest.latest_version == "1.1.0"
    assert len(manifest.versions) == 2
    with pytest.raises(ValueError):
        c.publish("summarize", "", SkillKind.OFFICIAL, "core", "0.9.0")


def test_install_remove_and_list():
    c = SkillCatalog()
    s = c.publish("code-gen", "generate code", SkillKind.COMMUNITY, "dev", "1.0.0")
    c.install(s.id, "agt_1")
    assert s.installs == 1
    assert [x.id for x in c.installed_by("agt_1")] == [s.id]
    assert c.remove(s.id, "agt_1") is True
    assert c.installed_by("agt_1") == []


def test_rate_and_recommend():
    c = SkillCatalog()
    a = c.publish("a", "a", SkillKind.COMMUNITY, "u", "1.0.0")
    b = c.publish("b", "b", SkillKind.COMMUNITY, "u", "1.0.0")
    c.rate(a.id, 5)
    c.rate(a.id, 4)
    assert c.get(a.id).rating == 4.5
    c.install(a.id, "agt_1")
    c.rate(b.id, 3)
    recs = c.recommend("agt_1")
    assert [r.id for r in recs] == [b.id]  # installed + unrated excluded


def test_search_by_query_and_kind():
    c = SkillCatalog()
    c.publish("translate", "translate text", SkillKind.ENTERPRISE, "corp", "1.0.0")
    c.publish("transcribe", "speech to text", SkillKind.OFFICIAL, "core", "1.0.0")
    assert len(c.search("trans")) == 2
    assert len(c.search(kind=SkillKind.ENTERPRISE)) == 1
