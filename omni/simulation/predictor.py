"""Failure prediction (M5b): train on execution traces, predict plan failure.

Backends
--------
* ``sklearn`` — RandomForestClassifier over a fixed 7-dimension feature
  vector (used automatically when scikit-learn is importable).
* ``fallback`` — pure-Python logistic regression (batch gradient descent)
  over standardized features, so the platform works without any ML
  dependency and degrades gracefully.

``generate_synthetic_traces`` seeds the predictor so it is usable out of the
box; real traces can be ingested through ``train()`` to retrain.
"""

from __future__ import annotations

import json
import math
import pickle
import random
import threading
from typing import Any

from omni.simulation.engine import SIMULATION_DOMAINS

FEATURE_NAMES = ["domain_idx", "size", "irreversible", "sensitive", "steps", "effects", "duration_s"]
RISK_LEVELS = [(0.15, "low"), (0.4, "medium"), (0.7, "high"), (1.01, "critical")]


def _sigmoid(z: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, z))))
    except OverflowError:  # pragma: no cover - defensive
        return 0.0 if z < 0 else 1.0


class _LogisticFallback:
    """Pure-Python logistic regression with standardized features."""

    def fit(self, X: list[list[float]], y: list[int], iters: int = 250, lr: float = 0.15) -> "_LogisticFallback":
        n = len(X)
        d = len(X[0])
        self.mean = [sum(r[i] for r in X) / n for i in range(d)]
        self.std = [math.sqrt(sum((r[i] - self.mean[i]) ** 2 for r in X) / n) for i in range(d)]
        self.std = [s if s > 1e-9 else 1.0 for s in self.std]
        Xs = [[(r[i] - self.mean[i]) / self.std[i] for i in range(d)] for r in X]
        self.w = [0.0] * (d + 1)
        for _ in range(iters):
            for r, yi in zip(Xs, y):
                err = _sigmoid(self._score(r)) - yi
                self.w[0] -= lr * err
                for i in range(d):
                    self.w[i + 1] -= lr * err * r[i]
        return self

    def _score(self, r: list[float]) -> float:
        return self.w[0] + sum(wi * xi for wi, xi in zip(self.w[1:], r))

    def predict_proba(self, X: list[list[float]]) -> list[float]:
        return [_sigmoid(self._score(r)) for r in X]

    def state(self) -> dict:
        return {"w": self.w, "mean": self.mean, "std": self.std}

    def load_state(self, state: dict) -> None:
        self.w = state["w"]
        self.mean = state["mean"]
        self.std = state["std"]


def _sample_features(sample: dict[str, Any], domains: list[str]) -> list[float]:
    domain = str(sample.get("domain", ""))
    try:
        di = domains.index(domain)
    except ValueError:
        di = 0
    size = min(float(sample.get("size", 1.0)), 3.0) / 3.0
    irreversible = 0.0 if sample.get("reversible", True) else 1.0
    sensitive = 1.0 if sample.get("sensitive_data", False) else 0.0
    steps = min(int(sample.get("num_steps", 1)), 20) / 20.0
    effects = min(int(sample.get("num_effects", 0)), 20) / 20.0
    duration = min(float(sample.get("duration_ms", 0)), 60000) / 60000.0
    return [di / max(len(domains), 1), size, irreversible, sensitive, steps, effects, duration]


def extract_features(plan: dict[str, Any], domains: list[str]) -> list[float]:
    """Feature vector for a plan dict (params + steps). Mirrors _sample_features."""
    params = plan.get("params") or {}
    steps = plan.get("steps") or []
    domain = str(plan.get("domain", ""))
    try:
        di = domains.index(domain)
    except ValueError:
        di = 0
    size = min(float(params.get("size", 1.0)), 3.0) / 3.0
    irreversible = 0.0 if params.get("reversible", True) else 1.0
    sensitive = 1.0 if params.get("sensitive_data", False) else 0.0
    n_steps = min(len(steps), 20) / 20.0
    n_effects = min(sum(len(s.get("effects", [])) for s in steps), 20) / 20.0
    duration = min(sum(float(s.get("duration_ms", 0)) for s in steps), 60000) / 60000.0
    return [di / max(len(domains), 1), size, irreversible, sensitive, n_steps, n_effects, duration]


def generate_synthetic_traces(n: int = 200, seed: int = 42, domains: list[str] | None = None) -> list[dict]:
    """Labeled synthetic execution traces for seeding / training the predictor.

    The labeling rule encodes realistic risk factors: sensitive data,
    irreversibility, domain bias (payments riskiest), effect count.
    """
    rng = random.Random(seed)
    domain_list = domains or sorted(SIMULATION_DOMAINS)
    bias = {
        "payment": 0.12, "db_migration": 0.08, "infra_change": 0.05,
        "email_campaign": 0.04, "refactor": 0.04, "file_delete": 0.03,
        "deploy": 0.02, "script_run": 0.02,
    }
    out: list[dict] = []
    for _ in range(n):
        domain = rng.choice(domain_list)
        size = rng.choice([0.2, 0.5, 1.0, 1.5, 2.5])
        reversible = rng.random() < 0.7
        sensitive = rng.random() < 0.25
        num_steps = rng.randint(2, 12)
        num_effects = rng.randint(num_steps, num_steps * 3)
        duration_ms = rng.uniform(50, 12000)
        p = (
            0.06
            + 0.12 * min(size, 3.0) / 3.0
            + 0.25 * float(sensitive)
            + 0.20 * (not reversible)
            + bias.get(domain, 0.03)
            + 0.05 * (num_effects >= 8)
        )
        p = min(0.92, p)
        label = 1 if rng.random() < p else 0
        out.append(
            {
                "domain": domain,
                "size": size,
                "reversible": reversible,
                "sensitive_data": sensitive,
                "num_steps": num_steps,
                "num_effects": num_effects,
                "duration_ms": round(duration_ms, 1),
                "label": label,
            }
        )
    return out


class FailurePredictor:
    """Train on execution traces and predict failure probability per plan."""

    def __init__(self, backend: str = "auto", domains: list[str] | None = None, seed: int = 42) -> None:
        self._lock = threading.RLock()
        self.domains = list(domains) if domains is not None else sorted(SIMULATION_DOMAINS)
        self._seed = seed
        self._trained = False
        self._n = 0
        self._model: Any = None
        self.backend = self._resolve(backend)

    def _resolve(self, backend: str) -> str:
        if backend == "auto":
            try:
                import sklearn  # noqa: F401

                return "sklearn"
            except Exception:
                return "fallback"
        if backend not in ("sklearn", "fallback"):
            raise ValueError(f"unknown backend '{backend}' (auto | sklearn | fallback)")
        if backend == "sklearn":
            try:
                import sklearn  # noqa: F401
            except Exception as exc:  # pragma: no cover - environment dependent
                raise ValueError("backend 'sklearn' requested but scikit-learn is not installed") from exc
        return backend

    # ------------------------------------------------------------ training
    def train(self, traces: list[dict]) -> "FailurePredictor":
        with self._lock:
            if not traces:
                raise ValueError("no traces to train on")
            X = [_sample_features(t, self.domains) for t in traces]
            y = [int(t["label"]) for t in traces]
            if self.backend == "sklearn":
                from sklearn.ensemble import RandomForestClassifier

                self._model = RandomForestClassifier(n_estimators=60, max_depth=5, random_state=self._seed)
                self._model.fit(X, y)
            else:
                self._model = _LogisticFallback().fit(X, y)
            self._trained = True
            self._n = len(traces)
            return self

    # ------------------------------------------------------------ prediction
    def predict(self, features: dict) -> dict:
        with self._lock:
            if not self._trained:
                return {
                    "trained": False,
                    "failure_probability": 0.0,
                    "risk_level": "low",
                    "predicted_failures": [],
                    "confidence": 0.0,
                    "features": features,
                }
            vector = [float(features.get(name, 0.0)) for name in FEATURE_NAMES]
            if self.backend == "sklearn":
                prob = float(self._model.predict_proba([vector])[0][1])
            else:
                prob = float(self._model.predict_proba([vector])[0])
            prob = round(max(0.0, min(1.0, prob)), 4)

            risk = "critical"
            for threshold, name in RISK_LEVELS:
                if prob < threshold:
                    risk = name
                    break

            catalog = SIMULATION_DOMAINS.get(str(features.get("domain", "")), {}).get("failures", [])
            if not catalog:
                catalog = ["generic action failure", "timeout or partial state", "unexpected exception"]
            if prob >= 0.5:
                predicted = list(catalog[:3])
            elif prob >= 0.25:
                predicted = list(catalog[:2])
            else:
                predicted = []

            return {
                "trained": True,
                "failure_probability": prob,
                "risk_level": risk,
                "predicted_failures": predicted,
                "confidence": round(max(prob, 1.0 - prob), 3),
                "features": features,
            }

    def predict_plan(self, plan: dict, domain: str = "generic") -> dict:
        extracted = extract_features(plan, self.domains)
        features = {"domain": domain}
        for name, value in zip(FEATURE_NAMES, extracted):
            features[name] = value
        out = self.predict(features)
        out["plan_name"] = plan.get("name")
        out["action"] = plan.get("action")
        return out

    # ------------------------------------------------------------ persistence
    def save(self, path: str) -> None:
        with self._lock:
            if self.backend == "fallback":
                payload = {
                    "backend": "fallback",
                    "state": self._model.state(),
                    "domains": self.domains,
                    "trained": self._trained,
                    "samples": self._n,
                }
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
            else:
                with open(path, "wb") as fh:
                    pickle.dump(
                        {
                            "backend": "sklearn",
                            "model": self._model,
                            "domains": self.domains,
                            "trained": self._trained,
                            "samples": self._n,
                        },
                        fh,
                    )

    def load(self, path: str) -> "FailurePredictor":
        with self._lock:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                self.backend = payload["backend"]
                self._model = _LogisticFallback()
                self._model.load_state(payload["state"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                with open(path, "rb") as fh:
                    payload = pickle.load(fh)
                self.backend = payload["backend"]
                self._model = payload["model"]
            self.domains = payload["domains"]
            self._trained = payload["trained"]
            self._n = payload["samples"]
            return self

    # ------------------------------------------------------------ introspection
    def model_info(self) -> dict:
        with self._lock:
            return {
                "backend": self.backend,
                "trained": self._trained,
                "samples": self._n,
                "feature_names": list(FEATURE_NAMES),
                "domains": list(self.domains),
            }
