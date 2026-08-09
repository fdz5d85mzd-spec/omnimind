"""OmniMind client SDK — talk to the control plane from inside the platform.

Agents use the SDK to submit tasks, install skills, call the policy engine,
write memory, run simulations, and read the twin. The transport is injectable
so tests run without a live server.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

Transport = Callable[[str, str, dict[str, Any] | None], Any]


def _http_transport(base_url: str, timeout: int = 10) -> Transport:
    base = base_url.rstrip("/")

    def transport(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode() if e.read else str(e)
            raise OmniApiError(e.code, path, detail) from e

    return transport


class OmniApiError(RuntimeError):
    def __init__(self, status: int, path: str, detail: str) -> None:
        super().__init__(f"OmniMind API {status} on {path}: {detail}")
        self.status = status
        self.path = path


class OmniClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", transport: Transport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport or _http_transport(self.base_url)

    # ------------------------------------------------------------ internal
    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._transport(method, path, payload)

    # ------------------------------------------------------------ policy
    def evaluate_policy(
        self,
        principal: dict[str, Any],
        action: str,
        resource: dict[str, Any] | None = None,
        cost: float = 0.0,
        calls: int = 1,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/policy/evaluate",
            {"principal": principal, "action": action, "resource": resource, "cost": cost, "calls": calls},
        )

    # ------------------------------------------------------------ memory
    def write_memory(self, key: str, value: dict[str, Any], agent_id: str, reason: str, branch: str = "main") -> dict[str, Any]:
        return self._call(
            "POST",
            "/memory/write",
            {"key": key, "value": value, "agent_id": agent_id, "reason": reason, "branch": branch},
        )

    def read_memory(self, key: str, branch: str = "main") -> dict[str, Any]:
        return self._call("GET", f"/memory/read?key={key}&branch={branch}")

    def memory_history(self, key: str, branch: str = "main") -> list[dict[str, Any]]:
        return self._call("GET", f"/memory/history?key={key}&branch={branch}")

    # ------------------------------------------------------------ orchestrator
    def register_agent(self, name: str, skills: list[str] | None = None) -> dict[str, Any]:
        return self._call("POST", "/agents/register", {"name": name, "skills": skills or []})

    def submit_task(
        self,
        name: str,
        required_skills: list[str] | None = None,
        priority: int = 0,
        risk_level: str = "low",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/tasks/submit",
            {
                "name": name,
                "required_skills": required_skills or [],
                "priority": priority,
                "risk_level": risk_level,
                "payload": payload or {},
            },
        )

    def assign_task(self, task_id: str) -> dict[str, Any]:
        return self._call("POST", f"/tasks/{task_id}/assign")

    def orchestrator_report(self) -> dict[str, Any]:
        return self._call("GET", "/orchestrator/report")

    # ------------------------------------------------------------ marketplace
    def publish_skill(
        self,
        name: str,
        description: str,
        kind: str,
        author: str,
        version: str,
        interface: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/marketplace/publish",
            {
                "name": name,
                "description": description,
                "kind": kind,
                "author": author,
                "version": version,
                "interface": interface,
                "tags": tags or [],
            },
        )

    def install_skill(self, skill_id: str, agent_id: str) -> dict[str, Any]:
        return self._call("POST", "/marketplace/install", {"skill_id": skill_id, "agent_id": agent_id})

    # ------------------------------------------------------------ simulation
    def simulate(self, action: str, domain: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call("POST", "/simulation/run", {"action": action, "domain": domain, "params": params})

    def dry_run(
        self,
        plan: dict[str, Any],
        domain: str = "generic",
        max_steps: int | None = None,
        timeout_s: float | None = None,
        max_effects: int | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/simulation/dryrun",
            {
                "plan": plan,
                "domain": domain,
                "max_steps": max_steps,
                "timeout_s": timeout_s,
                "max_effects": max_effects,
            },
        )

    def predict_failure(self, plan: dict[str, Any], domain: str = "generic") -> dict[str, Any]:
        return self._call("POST", "/simulation/predict", {"plan": plan, "domain": domain})

    def predictor_status(self) -> dict[str, Any]:
        return self._call("GET", "/simulation/predictor/status")

    def train_predictor(self, traces: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", "/simulation/predictor/train", {"traces": traces})

    # ------------------------------------------------------------ observability
    def twin_snapshot(self) -> dict[str, Any]:
        return self._call("GET", "/twin/snapshot")

    def learning_report(self) -> dict[str, Any]:
        return self._call("GET", "/learning/report")

    def replay(self, subject: str | None = None, subsystem: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        path = "/twin/replay"
        parts = []
        if subject is not None:
            parts.append(f"subject={subject}")
        if subsystem is not None:
            parts.append(f"subsystem={subsystem}")
        if limit != 100:
            parts.append(f"limit={limit}")
        if parts:
            path += "?" + "&".join(parts)
        return self._call("GET", path)
