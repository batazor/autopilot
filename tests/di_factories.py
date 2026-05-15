"""Test helpers for constructing services without Dishka."""

from __future__ import annotations

from pathlib import Path

from config.loader import Settings, load_settings
from scenarios.cron_specs import scenario_loader_paths
from scenarios.evaluator import ScenarioEvaluator
from scenarios.loader import ScenarioLoader
from scheduler.optimizer import TaskOptimizer
from scheduler.runner import SchedulerRunner


def make_scheduler_runner(settings: Settings | None = None) -> SchedulerRunner:
    cfg = settings if settings is not None else load_settings()
    repo_root = Path(__file__).resolve().parent.parent
    return SchedulerRunner(
        cfg,
        ScenarioLoader(scenario_loader_paths(repo_root)),
        TaskOptimizer(cfg),
        ScenarioEvaluator(),
    )
