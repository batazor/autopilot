from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from config.devices import get_device_registry

if TYPE_CHECKING:
    from dsl.models import Scenario, StepCondition
    from tasks.base import BaseTask

logger = logging.getLogger(__name__)

_TASK_FACTORIES: dict[str, type] = {}


def _coerce_int(value: object, default: int = 0) -> int:
    """Best-effort ``int`` parse for YAML-loaded condition values (``object``)."""
    if value is None:
        return default
    if isinstance(value, (int, float, str, bytes, bytearray)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


class ScenarioEvaluator:
    def evaluate_conditions(
        self,
        conditions: list[StepCondition],
        player_state: dict[str, object],
    ) -> bool:
        now = datetime.now(tz=UTC)

        for cond in conditions:
            match cond.type:
                case "time_range":
                    from_t = str(cond.from_ or "00:00")
                    to_t = str(cond.to or "23:59")
                    hm_now = now.strftime("%H:%M")
                    # Overnight window (from > to, e.g. 22:00→02:00) wraps
                    # midnight: now must be ≥ from OR ≤ to. Same-day window
                    # is the usual ``from ≤ now ≤ to``.
                    in_range = (
                        from_t <= hm_now <= to_t
                        if from_t <= to_t
                        else hm_now >= from_t or hm_now <= to_t
                    )
                    if not in_range:
                        return False
                case "player_level_min":
                    player_id = str(player_state.get("player_id", ""))
                    gamer = get_device_registry().get_gamer(player_id)
                    level = gamer.level if gamer else 0
                    if level < _coerce_int(cond.value):
                        return False
                case "resource_min":
                    resource_val = _coerce_int(player_state.get(str(cond.resource or "")))
                    if resource_val < _coerce_int(cond.value):
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
