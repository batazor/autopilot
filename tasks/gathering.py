from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import UTC, datetime, timedelta

from actions.tap import BotActions
from layout import screens
from navigation.detector import ScreenName
from navigation.navigator import Navigator
from ocr.client import OcrClient
from ocr.fuzzy import match
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class GatheringTask:
    task_id: str
    player_id: str
    resources: list[str] = field(default_factory=lambda: ["wood", "food"])
    march_slots: str = "all"
    priority: int = 300
    cooldown_seconds: int = 7200
    is_cooperative: bool = False
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="gathering", init=False)

    def estimate_duration(self) -> int:
        return 90

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=actions.capture_screen_bgr,
            tap_fn=actions.tap,
            redis_client=self.redis_client,
        )

        ok = await navigator.navigate_to(ScreenName.GATHERING, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=UTC) + timedelta(minutes=15),
            )

        image = actions.capture_screen_bgr(instance_id)
        march_result = await ocr.ocr_region(image, screens.GATHERING.march_slots_region)
        if match(march_result.text, ["full", "no slots", "marching"]):
            logger.info("All march slots busy on %s", instance_id)
            return TaskResult(
                success=True,
                next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            )

        marches_sent = 0
        resource_nodes = {
            "wood": screens.GATHERING.wood_node,
            "food": screens.GATHERING.food_node,
        }

        for resource in self.resources:
            node = resource_nodes.get(resource)
            if node is None:
                continue
            actions.tap(instance_id, node)
            await asyncio.sleep(1.5)
            actions.tap(instance_id, screens.GATHERING.send_march_btn)
            await asyncio.sleep(2.0)
            marches_sent += 1

        logger.info(
            "Gathering: %d marches sent on %s/%s", marches_sent, instance_id, self.player_id
        )
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            metadata={"marches_sent": marches_sent},
        )
