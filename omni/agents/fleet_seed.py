"""The standing fleet roster: 40 named agents with real, distinct skills,
led by one supervisor. Registered through the same MetaOrchestrator API as
any other agent — nothing here is decorative. `seed_fleet()` is idempotent
by name, so calling it again (e.g. on every process restart) never
duplicates the roster.
"""

from __future__ import annotations

from omni.contracts.agent import AgentSpec, AgentType
from omni.orchestrator.engine import MetaOrchestrator

LEADER_NAME = "Atlas"

# (name, agent_type, skills) — one SUPERVISOR (the leader) plus 39
# WORKER/SPECIALIST agents spanning the product's real subsystems and the
# general operational roles an autonomous fleet actually needs.
SEED_ROSTER: list[tuple[str, AgentType, list[str]]] = [
    (LEADER_NAME, AgentType.SUPERVISOR, ["coordination", "task-routing", "fleet-leadership"]),
    ("Research Agent", AgentType.SPECIALIST, ["research", "web-search", "fact-checking"]),
    ("Writer Agent", AgentType.SPECIALIST, ["writing", "editing", "copywriting"]),
    ("Code Agent", AgentType.SPECIALIST, ["coding", "code-review", "debugging"]),
    ("Planner Agent", AgentType.SPECIALIST, ["planning", "task-breakdown", "scheduling"]),
    ("Memory Curator", AgentType.WORKER, ["memory", "curation", "indexing"]),
    ("Policy Auditor", AgentType.SPECIALIST, ["policy", "compliance", "audit"]),
    ("Security Monitor", AgentType.SPECIALIST, ["security", "threat-detection", "monitoring"]),
    ("DevOps Agent", AgentType.WORKER, ["deployment", "infrastructure", "ci-cd"]),
    ("Marketplace Curator", AgentType.WORKER, ["marketplace", "skill-review", "curation"]),
    ("QA Agent", AgentType.WORKER, ["testing", "qa", "validation"]),
    ("Support Agent", AgentType.WORKER, ["customer-support", "triage", "communication"]),
    ("Docs Agent", AgentType.WORKER, ["documentation", "technical-writing"]),
    ("Translator", AgentType.SPECIALIST, ["translation", "localization"]),
    ("Data Analyst", AgentType.SPECIALIST, ["data-analysis", "statistics", "reporting"]),
    ("Performance Monitor", AgentType.WORKER, ["performance", "monitoring", "alerting"]),
    ("Billing Agent", AgentType.WORKER, ["billing", "credits", "accounting"]),
    ("Notification Agent", AgentType.WORKER, ["notifications", "messaging"]),
    ("Search Agent", AgentType.WORKER, ["search", "retrieval", "ranking"]),
    ("Summarizer", AgentType.WORKER, ["summarization", "compression"]),
    ("Integration Agent", AgentType.SPECIALIST, ["api-integration", "webhooks"]),
    ("Incident Responder", AgentType.SPECIALIST, ["incident-response", "on-call", "triage"]),
    ("Knowledge Graph Curator", AgentType.SPECIALIST, ["knowledge-graph", "relationships", "curation"]),
    ("Task Triage Agent", AgentType.WORKER, ["triage", "prioritization"]),
    ("Load Balancer Agent", AgentType.WORKER, ["load-balancing", "scaling"]),
    ("Backup Agent", AgentType.WORKER, ["backup", "recovery", "snapshots"]),
    ("Audit Logger", AgentType.WORKER, ["audit", "logging", "compliance"]),
    ("Model Evaluator", AgentType.SPECIALIST, ["evaluation", "benchmarking", "quality"]),
    ("Prompt Engineer", AgentType.SPECIALIST, ["prompt-engineering", "optimization"]),
    ("Voice Agent", AgentType.SPECIALIST, ["voice", "transcription", "speech"]),
    ("Localization Agent", AgentType.WORKER, ["localization", "i18n"]),
    ("Analytics Agent", AgentType.WORKER, ["analytics", "metrics", "dashboards"]),
    ("Reporting Agent", AgentType.WORKER, ["reporting", "summaries"]),
    ("Escalation Agent", AgentType.WORKER, ["escalation", "routing"]),
    ("Onboarding Agent", AgentType.WORKER, ["onboarding", "user-guidance"]),
    ("Simulation Agent", AgentType.SPECIALIST, ["simulation", "what-if-analysis"]),
    ("Learning Agent", AgentType.SPECIALIST, ["learning", "evaluation", "trends"]),
    ("Fleet Health Agent", AgentType.WORKER, ["health-check", "diagnostics"]),
    ("Cost Optimizer", AgentType.WORKER, ["cost-optimization", "budgeting"]),
    ("Compliance Officer", AgentType.SPECIALIST, ["compliance", "legal", "policy"]),
]

assert len(SEED_ROSTER) == 40
assert sum(1 for _, t, _ in SEED_ROSTER if t == AgentType.SUPERVISOR) == 1


def seed_fleet(orchestrator: MetaOrchestrator) -> list[AgentSpec]:
    """Register every agent in SEED_ROSTER not already present (by name).
    Safe to call on every startup — existing agents are left untouched."""
    existing_names = {a.name for a in orchestrator.agents()}
    created: list[AgentSpec] = []
    for name, agent_type, skills in SEED_ROSTER:
        if name in existing_names:
            continue
        created.append(orchestrator.register_agent(name, skills, agent_type))
    return created


def leader_id(orchestrator: MetaOrchestrator) -> str | None:
    for agent in orchestrator.agents():
        if agent.name == LEADER_NAME:
            return agent.id
    return None
