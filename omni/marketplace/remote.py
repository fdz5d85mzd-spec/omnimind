"""Remote skill sync (M4b): pull skills from remote skill registries.

A remote registry exposes a JSON manifest at its URL::

    {
      "name": "code-gen",
      "description": "generate code",
      "author": "vendor",
      "kind": "enterprise",
      "tags": ["codegen"],
      "versions": [
        {"version": "1.0.0", "changelog": "initial",
         "interface": {"actions": ["generate"], "events": [],
                       "permissions": ["files.write"], "config_schema": {},
                       "documentation": "", "tests": [],
                       "ui_components": [], "api": []}}
      ]
    }

`RemoteSkillRegistry.sync(url)` fetches the manifest, publishes every version in
ascending order (the catalog already rejects non-newer versions), and stamps
`source_url` + `last_synced` on the manifest so provenance is never lost.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from omni.contracts.skill import SkillInterface, SkillKind, SkillManifest

Fetcher = Callable[[str], dict[str, Any]]


class RemoteRegistryError(RuntimeError):
    """Raised when a remote skill registry is unreachable or malformed."""


def _http_fetch(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.split(".")[:3])
    except ValueError:
        return (0, 0, 0)


class RemoteSkillRegistry:
    def __init__(self, catalog, fetcher: Fetcher | None = None) -> None:
        self._catalog = catalog
        self._fetcher = fetcher or _http_fetch
        self._lock = threading.RLock()
        self._sources: list[str] = []
        self._synced: dict[str, datetime] = {}

    # ------------------------------------------------------------ sources
    def register_source(self, url: str) -> None:
        with self._lock:
            if url not in self._sources:
                self._sources.append(url)

    def sources(self) -> list[str]:
        with self._lock:
            return list(self._sources)

    def last_synced(self, url: str) -> datetime | None:
        with self._lock:
            return self._synced.get(url)

    # ------------------------------------------------------------ sync
    def sync_all(self) -> list[SkillManifest]:
        results = [self.sync(url) for url in self.sources()]
        return [r for r in results if r is not None]

    def sync(self, url: str) -> SkillManifest | None:
        """Fetch the remote manifest and publish any newer versions."""
        raw = self._fetcher(url)
        name = raw.get("name")
        if not name:
            raise RemoteRegistryError(f"registry at '{url}' has no 'name'")
        versions = raw.get("versions") or []
        if not versions:
            raise RemoteRegistryError(f"registry at '{url}' has no versions")

        published: SkillManifest | None = None
        for meta in sorted(versions, key=lambda v: _version_key(v.get("version", "0.0.0"))):
            version = meta.get("version")
            if not version:
                continue
            interface = SkillInterface(**meta.get("interface", {}))
            try:
                manifest = self._catalog.publish(
                    name=name,
                    description=raw.get("description", ""),
                    kind=SkillKind(raw.get("kind", "remote")),
                    author=raw.get("author", "remote"),
                    version=version,
                    interface=interface,
                    tags=raw.get("tags", []),
                )
            except ValueError:
                continue  # older version — skip silently
            manifest.source_url = url
            manifest.last_synced = datetime.now(timezone.utc)
            published = manifest

        if published is None:
            raise RemoteRegistryError(f"no new versions at '{url}'")
        with self._lock:
            self._synced[url] = published.last_synced or datetime.now(timezone.utc)
        return published
