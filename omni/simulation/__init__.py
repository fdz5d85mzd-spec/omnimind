"""Simulation Sandbox — simulate before you touch (engines, dry-run, ML)."""

from omni.simulation.engine import SIMULATION_DOMAINS, SimulationSandbox
from omni.simulation.predictor import FailurePredictor, generate_synthetic_traces
from omni.simulation.runner import (
    DEFAULT_ALLOWED_EFFECTS,
    DryRunExecutor,
    DryRunResult,
    NoopEffectBackend,
)

__all__ = [
    "SimulationSandbox",
    "SIMULATION_DOMAINS",
    "DryRunExecutor",
    "DryRunResult",
    "NoopEffectBackend",
    "DEFAULT_ALLOWED_EFFECTS",
    "FailurePredictor",
    "generate_synthetic_traces",
]
