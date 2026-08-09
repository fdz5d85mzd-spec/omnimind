"""Remote skill sync (M4b) tests."""

import pytest

from omni.contracts.skill import SkillKind
from omni.marketplace.catalog import SkillCatalog
from omni.marketplace.remote import RemoteRegistryError, RemoteSkillRegistry


def _manifest_empty():
    return {"name": "empty-registry", "versions": []}


def _manifest_two_versions():
    return {
        "name": "code-gen",
        "description": "generate code",
        "author": "vendor",
        "kind": "enterprise",
        "tags": ["codegen"],
        "versions": [
            {
                "version": "1.0.0",
                "changelog": "initial",
                "interface": {
                    "actions": ["generate"],
                    "events": [],
                    "permissions": ["files.write"],
                    "config_schema": {},
                    "documentation": "",
                    "tests": [],
                    "ui_components": [],
                    "api": [],
                },
            },
            {"version": "1.1.0", "changelog": "batch mode", "interface": {"actions": ["generate", "generate_many"]}},
        ],
    }


def test_sync_publishes_all_versions():
    catalog = SkillCatalog()
    registry = RemoteSkillRegistry(catalog, fetcher=lambda url: _manifest_two_versions())
    manifest = registry.sync("https://registry.example/skills/code-gen.json")
    assert manifest.latest_version == "1.1.0"
    assert len(manifest.versions) == 2
    assert manifest.kind == SkillKind.ENTERPRISE
    assert manifest.source_url == "https://registry.example/skills/code-gen.json"
    assert manifest.source_url  # provenance stamped
    assert manifest.last_synced is not None


def test_sync_then_duplicate_sync_is_noop():
    catalog = SkillCatalog()
    registry = RemoteSkillRegistry(catalog, fetcher=lambda url: _manifest_two_versions())
    manifest = registry.sync("https://registry.example/skills/code-gen.json")
    assert manifest.latest_version == "1.1.0"
    # second sync: no newer versions → raises RemoteRegistryError (safe no-op)
    with pytest.raises(RemoteRegistryError):
        registry.sync("https://registry.example/skills/code-gen.json")
    # the catalog is unchanged
    assert catalog.get(manifest.id).latest_version == "1.1.0"


def test_register_sources_and_sync_all():
    catalog = SkillCatalog()
    registry = RemoteSkillRegistry(catalog, fetcher=lambda url: _manifest_two_versions())
    registry.register_source("https://registry.example/skills/code-gen.json")
    assert registry.sources() == ["https://registry.example/skills/code-gen.json"]
    synced = registry.sync_all()
    assert len(synced) == 1
    assert synced[0].name == "code-gen"


def test_empty_registry_rejected():
    catalog = SkillCatalog()
    registry = RemoteSkillRegistry(catalog, fetcher=lambda url: _manifest_empty())
    with pytest.raises(RemoteRegistryError):
        registry.sync("https://registry.example/x")
