"""Skill Marketplace catalog: publish, discover, install, upgrade, remove,
rate, recommend. Skills carry a kind (official / community / private /
enterprise / local / remote) and a full version history.

NOT IMPLEMENTED (roadmap M4b): remote-skill sync adapters (git/registry),
skill sandboxing (permissions enforcement at runtime), UI component registry
delivery. The catalog itself is complete and tested.
"""

from __future__ import annotations

import threading
from collections import defaultdict

from omni.contracts.skill import SkillInterface, SkillKind, SkillManifest, SkillVersion


class SkillCatalog:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._skills: dict[str, SkillManifest] = {}
        self._installations: dict[str, set[str]] = defaultdict(set)  # agent_id -> skill ids

    # ------------------------------------------------------------ lifecycle
    def publish(
        self,
        name: str,
        description: str,
        kind: SkillKind,
        author: str,
        version: str,
        interface: SkillInterface | None = None,
        tags: list[str] | None = None,
    ) -> SkillManifest:
        with self._lock:
            kind = SkillKind(kind) if isinstance(kind, str) else kind
            existing = next((s for s in self._skills.values() if s.name == name), None)
            if existing is None:
                manifest = SkillManifest(
                    name=name,
                    description=description,
                    kind=kind,
                    author=author,
                    tags=tags or [],
                )
                self._skills[manifest.id] = manifest
            else:
                manifest = existing
                manifest.description = description or manifest.description
                manifest.kind = kind
                manifest.author = author or manifest.author
                if tags:
                    manifest.tags = sorted(set(manifest.tags) | set(tags))
            self._add_version(manifest, version, interface)
            return manifest

    def _add_version(self, manifest: SkillManifest, version: str, interface: SkillInterface | None) -> None:
        if materialize(manifest.latest_version) >= materialize(version):
            raise ValueError(f"version '{version}' is not newer than '{manifest.latest_version}'")
        manifest.versions.append(
            SkillVersion(version=version, interface=interface or SkillInterface())
        )
        manifest.latest_version = version

    def install(self, skill_id: str, agent_id: str, version: str | None = None) -> SkillManifest | None:
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                return None
            if version is not None:
                if not any(v.version == version for v in skill.versions):
                    raise KeyError(f"skill '{skill.name}' has no version '{version}'")
            skill.installs += 1
            self._installations[agent_id].add(skill_id)
            return skill

    def remove(self, skill_id: str, agent_id: str) -> bool:
        with self._lock:
            if skill_id not in self._installations.get(agent_id, set()):
                return False
            self._installations[agent_id].discard(skill_id)
            return True

    def upgrade(self, agent_id: str) -> list[SkillManifest]:
        """No-op signal: upgrades re-install the newest version (version chosen at install)."""
        with self._lock:
            return [self._skills[sid] for sid in self._installations.get(agent_id, set())]

    # ------------------------------------------------------------ share
    def export_manifest(self, skill_id: str) -> dict:
        """Serializable snapshot for sharing / publishing a skill."""
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                raise KeyError(f"no skill '{skill_id}'")
            return skill.model_dump(mode="json")

    def import_manifest(self, data: dict) -> SkillManifest:
        """Install a shared manifest (JSON) as an independent skill copy."""
        with self._lock:
            manifest = SkillManifest(**data)
            if manifest.id in self._skills:
                raise ValueError(f"skill '{manifest.name}' already exists in this catalog")
            versions = [materialize(v.version) for v in manifest.versions]
            if versions != sorted(versions):
                raise ValueError("skill versions are not in ascending order")
            self._skills[manifest.id] = manifest
            return manifest

    # ------------------------------------------------------------ queries
    def get(self, skill_id: str) -> SkillManifest | None:
        with self._lock:
            return self._skills.get(skill_id)

    def search(self, query: str = "", kind: SkillKind | None = None) -> list[SkillManifest]:
        with self._lock:
            kind = SkillKind(kind) if isinstance(kind, str) else kind
            q = query.lower()
            out = []
            for skill in self._skills.values():
                if kind is not None and skill.kind is not kind:
                    continue
                if q and q not in skill.name.lower() and q not in skill.description.lower() and not any(q in t.lower() for t in skill.tags):
                    continue
                out.append(skill)
            return out

    def all(self) -> list[SkillManifest]:
        with self._lock:
            return list(self._skills.values())

    def installed_by(self, agent_id: str) -> list[SkillManifest]:
        with self._lock:
            return [self._skills[sid] for sid in self._installations.get(agent_id, set()) if sid in self._skills]

    # ------------------------------------------------------------ ratings & recommend
    def rate(self, skill_id: str, stars: int) -> float:
        stars = max(1, min(5, stars))
        with self._lock:
            skill = self._skills[skill_id]
            skill.rating_sum += stars
            skill.rating_count += 1
            return skill.rating

    def recommend(self, agent_id: str, top: int = 5) -> list[SkillManifest]:
        """Simple collaborative heuristic: top-rated skills not yet installed."""
        with self._lock:
            installed = self._installations.get(agent_id, set())
            ranked = sorted(
                (s for s in self._skills.values() if s.id not in installed and s.rating_count > 0),
                key=lambda s: (s.rating, s.installs),
                reverse=True,
            )
            return ranked[:top]


def materialize(version: str) -> tuple[int, ...]:
    """'1.2.3' -> (1, 2, 3) for comparison."""
    return tuple(int(part) for part in version.split(".")[:3])
