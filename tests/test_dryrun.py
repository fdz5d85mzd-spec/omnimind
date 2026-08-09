"""Dry-run sandbox (M5b) tests: structural zero-side-effect execution,
allow-list enforcement, and step/time/effect budgets."""

import os

import pytest

from omni.simulation.runner import DryRunExecutor, NoopEffectBackend

CLEAN_PLAN = {
    "name": "deploy",
    "params": {"size": 0.5},
    "steps": [
        {"name": "validate", "action": "plan.validate", "effects": ["plan.validate"], "duration_ms": 5},
        {"name": "apply", "action": "migration.apply", "effects": ["migration.apply", "config.write"], "duration_ms": 10},
    ],
}


def test_noop_backend_records_without_touching_the_system():
    backend = NoopEffectBackend()
    assert backend.apply("files.write", {"path": "/etc/omnimind/sentinel"})["dry_run"] is True
    assert len(backend.log) == 1
    assert not os.path.exists("/etc/omnimind/sentinel")  # nothing was actually written
    # the backend has no system-access surface
    assert not hasattr(backend, "_os") and not hasattr(backend, "_subprocess")


def test_clean_plan_runs_fully():
    backend = NoopEffectBackend()
    result = DryRunExecutor(backend=backend).run(CLEAN_PLAN, domain="db_migration")
    assert result.verdict == "clean"
    assert result.steps_total == 2
    assert result.steps_ok == 2
    assert result.steps_failed == 0
    assert result.effects_total == 3
    assert len(backend.log) == 3  # every effect was simulated and recorded


def test_simulated_failure_recorded_and_effects_dropped():
    plan = {
        "name": "migration",
        "steps": [
            {"name": "backfill", "action": "migration.backfill", "effects": ["migration.backfill"], "duration_ms": 5},
            {"name": "swap", "action": "migration.apply", "effects": ["migration.apply"], "duration_ms": 10,
             "error": "simulated: index build timeout"},
        ],
    }
    result = DryRunExecutor().run(plan, domain="db_migration")
    assert result.verdict == "has_failures"
    assert result.steps_failed == 1
    failed_step = result.steps[1]
    assert failed_step.status == "failed"
    assert "index build timeout" in failed_step.error
    assert failed_step.effects_applied == []  # failed step never applies effects


def test_unknown_effect_type_rejected_structurally():
    plan = {
        "name": "x",
        "steps": [{"name": "escape", "action": "rm", "effects": ["rm.system"], "duration_ms": 5}],
    }
    backend = NoopEffectBackend()
    result = DryRunExecutor(backend=backend).run(plan)
    assert result.verdict == "has_failures"
    assert result.rejected_effects == ["rm.system"]
    assert result.steps[0].status == "failed"
    assert "not allowed" in result.steps[0].error
    assert backend.log == []  # the rejected effect was structurally never applied


def test_effect_budget_skips_remaining_steps():
    plan = {
        "name": "big",
        "steps": [
            {"name": "a", "effects": ["config.write", "state.write"], "duration_ms": 5},
            {"name": "b", "effects": ["config.write"], "duration_ms": 5},
        ],
    }
    result = DryRunExecutor(max_effects=2).run(plan)
    assert result.verdict == "budget_exceeded"
    assert result.effects_total == 2
    assert result.steps_ok == 1
    assert result.steps[1].status == "skipped"
    assert result.steps[1].reason == "effect budget exceeded"
    assert any("effect budget" in r for r in result.budget_reasons)


def test_timeout_stops_the_run():
    plan = {
        "name": "slow",
        "steps": [
            {"name": "a", "effects": ["plan.validate"], "duration_ms": 3},
            {"name": "b", "effects": ["plan.run"], "duration_ms": 3},
        ],
    }
    result = DryRunExecutor(timeout_s=0.004).run(plan)  # 4ms simulated budget
    assert result.timeout_hit is True
    assert result.steps_ok == 1
    assert result.steps[1].status == "skipped"
    assert result.steps[1].reason == "timeout"
    assert result.verdict == "budget_exceeded"


def test_step_limit_skips_excess_steps():
    plan = {
        "name": "many",
        "steps": [
            {"name": f"s{i}", "effects": ["plan.validate"], "duration_ms": 1} for i in range(5)
        ],
    }
    result = DryRunExecutor(max_steps=2).run(plan)
    assert result.steps_total == 5
    assert result.steps_ok == 2
    assert result.steps_skipped == 3
    assert result.steps[2].reason == "step limit exceeded"
    assert result.verdict == "budget_exceeded"


def test_empty_plan_rejected():
    with pytest.raises(ValueError):
        DryRunExecutor().run({"name": "empty", "steps": []})
