from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from config.loader import Settings
    from tasks.base import BaseTask

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizationInput:
    player_tasks: dict[str, list[BaseTask]]
    player_instance_map: dict[str, str]


class TaskOptimizer:
    """OR-Tools CP-SAT optimizer for multi-player, multi-instance task assignment."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def optimize(self, inp: OptimizationInput) -> dict[str, list[BaseTask]]:
        timeout = self._settings.scheduler.ortools_timeout_seconds

        players = list(inp.player_tasks.keys())
        if not players:
            return {}

        # Build flat task list per player
        all_tasks: list[tuple[str, BaseTask, int]] = [
            (player_id, task, task.priority)
            for player_id, tasks in inp.player_tasks.items()
            for task in tasks
        ]

        if not all_tasks:
            return {pid: [] for pid in players}

        model = cp_model.CpModel()

        # x[i] = 1 if task i is assigned
        x: list[cp_model.IntVar] = [
            model.new_bool_var(f"x_{i}_{pid}_{t.task_type}")
            for i, (pid, t, _) in enumerate(all_tasks)
        ]

        # Constraint: each player executes at most one task at a time
        for player_id in players:
            player_vars = [
                x[i]
                for i, (pid, _t, _p) in enumerate(all_tasks)
                if pid == player_id
            ]
            if player_vars:
                model.add(sum(player_vars) <= 1)

        # Constraint: instance serialization — players on same instance can't run simultaneously
        instances: dict[str, list[str]] = {}
        for player_id, instance_id in inp.player_instance_map.items():
            instances.setdefault(instance_id, []).append(player_id)

        for instance_players in instances.values():
            instance_vars = [
                x[i]
                for i, (pid, _t, _p) in enumerate(all_tasks)
                if pid in instance_players
            ]
            if instance_vars:
                model.add(sum(instance_vars) <= 1)

        # Constraint: cooperative tasks claimed by at most one player total
        cooperative_types: set[str] = {
            t.task_type
            for _pid, t, _p in all_tasks
            if t.is_cooperative
        }
        for task_type in cooperative_types:
            coop_vars = [
                x[i]
                for i, (_pid, t, _p) in enumerate(all_tasks)
                if t.task_type == task_type and t.is_cooperative
            ]
            if coop_vars:
                model.add(sum(coop_vars) <= 1)

        # Objective: maximize weighted sum of priorities
        model.maximize(
            sum(x[i] * priority for i, (_pid, _t, priority) in enumerate(all_tasks))
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = timeout
        status = solver.solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            logger.warning("OR-Tools found no feasible solution (status=%s)", status)
            return {}

        result: dict[str, list[BaseTask]] = {pid: [] for pid in players}
        for i, (player_id, task, _) in enumerate(all_tasks):
            if solver.value(x[i]):
                result[player_id].append(task)

        return result
