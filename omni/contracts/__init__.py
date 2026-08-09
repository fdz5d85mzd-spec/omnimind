"""Shared typed contracts — the OmniMind constitution.

Every subsystem imports these schemas; nothing redefines them.
"""

from omni.contracts.agent import (
    AgentSpec,
    AgentStatus,
    AgentType,
    TaskSpec,
    TaskStatus,
)
from omni.contracts.evaluation import Evaluation, MetricBundle
from omni.contracts.memory import (
    BranchRequest,
    MemoryDiff,
    MemoryDiffItem,
    MemoryDiffOp,
    MemoryEntry,
    RollbackRequest,
)
from omni.contracts.policy import (
    ABACCondition,
    ApprovalStatus,
    LimitPolicy,
    LimitStatus,
    PolicyDecision,
    PolicyRule,
    Principal,
    Resource,
    RiskLevel,
)
from omni.contracts.simulation import SimulationResult
from omni.contracts.skill import SkillInterface, SkillKind, SkillManifest, SkillVersion

__all__ = [
    "AgentSpec",
    "AgentStatus",
    "AgentType",
    "TaskSpec",
    "TaskStatus",
    "Evaluation",
    "MetricBundle",
    "BranchRequest",
    "MemoryDiff",
    "MemoryDiffItem",
    "MemoryDiffOp",
    "MemoryEntry",
    "RollbackRequest",
    "ABACCondition",
    "ApprovalStatus",
    "LimitPolicy",
    "LimitStatus",
    "PolicyDecision",
    "PolicyRule",
    "Principal",
    "Resource",
    "RiskLevel",
    "SimulationResult",
    "SkillInterface",
    "SkillKind",
    "SkillManifest",
    "SkillVersion",
]
