from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from config.loader import get_settings
from fsm.states import PlayerState
from scenarios.models import Scenario, ScenarioStep, StepCondition
from tasks.arena import ArenaTask
from tasks.base import BaseTask
from tasks.beast import BeastTask
from tasks.daily import DailyCheckinTask
from tasks.defend import DefendAllyTask
from tasks.gathering import GatheringTask
from tasks.training import TrainingTask

logger = logging.getLogger(__name__)

_TASK_FACTORIES: dict[str, type] = {
    "arena": ArenaTask,
    "training": TrainingTask,
    "gathering": GatheringTask,
    "daily_checkin": DailyCheckinTask,
    "defend_ally": DefendAllyTask,
    "beast": BeastTask,
}


class ScenarioEvaluator:
    def evaluate_conditions(
        self,
        conditions: list[StepCondition],
        player_state: dict[str, object],
    ) -> bool:
        now = datetime.now(tz=timezone.utc)
        settings = get_settings()

        for cond in conditions:
            match cond.type:
                case "time_range":
                    from_t = str(cond.from_ or "00:00")
                    to_t = str(cond.to or "23:59")
                    hm_now = now.strftime("%H:%M")
                    if not (from_t <= hm_now <= to_t):
                        return False
                case "player_level_min":
                    player_id = str(player_state.get("player_id", ""))
                    pcfg = settings.players.get(player_id)
                    level = pcfg.level if pcfg else 0
                    if level < int(cond.value or 0):
                        return False
                case "resource_min":
                    resource_val = int(player_state.get(str(cond.resource or ""), 0))
                    if resource_val < int(cond.value or 0):
                        return False
                case "alliance_member_under_attack":
                    # Evaluated at runtime by the task itself
                    pass
                case _:
                    logger.warning("Unknown condition type: %s", cond.type)
        return True

    def expand_to_tasks(
        self,
        scenario: Scenario,
        player_state: dict[str, object],
    ) -> list[BaseTask]:
        if not scenario.enabled:
            return []

        player_id = str(player_state.get("player_id", ""))
        settings = get_settings()

        if not self.evaluate_conditions(scenario.conditions, player_state):
            return []

        tasks: list[BaseTask] = []
        for step in scenario.steps:
            if not self.evaluate_conditions(step.conditions, player_state):
                continue

            factory = _TASK_FACTORIES.get(step.task)
            if factory is None:
                logger.warning("Unknown task type: %s", step.task)
                continue

            task_cfg = settings.tasks.get(step.task)
            cooldown = int(step.cooldown.total_seconds())

            task_kwargs: dict[str, object] = {
                "task_id": f"{player_id}:{step.task}:{uuid.uuid4().hex[:8]}",
                "player_id": player_id,
                "priority": scenario.priority * step.priority,
                "cooldown_seconds": cooldown,
            }

            # Merge step params
            if step.task == "training" and step.params.troop_type:
                task_kwargs["troop_type"] = step.params.troop_type
            if step.task == "gathering":
                if step.params.resources:
                    task_kwargs["resources"] = step.params.resources
                if step.params.march_slots:
                    task_kwargs["march_slots"] = step.params.march_slots

            try:
                task = factory(**task_kwargs)
                tasks.append(task)  # type: ignore[arg-type]
            except Exception:
                logger.exception("Failed to create task %s for player %s", step.task, player_id)

        return tasks
