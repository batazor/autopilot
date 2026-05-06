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
class TrainingTask:
    task_id: str
    player_id: str
    troop_type: str = "infantry"
    quantity: str = "auto"
    priority: int = 400
    cooldown_seconds: int = 1800
    is_cooperative: bool = False
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="training", init=False)

    def estimate_duration(self) -> int:
        return 60

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=actions.capture_screen_bgr,
            tap_fn=actions.tap,
            redis_client=self.redis_client,
        )

        ok = await navigator.navigate_to(ScreenName.TRAINING, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=UTC) + timedelta(minutes=15),
            )

        # Select troop type tab
        tab_map = {
            "infantry": screens.TRAINING.infantry_tab,
            "lancer": screens.TRAINING.lancer_tab,
            "marksman": screens.TRAINING.marksman_tab,
        }
        tab = tab_map.get(self.troop_type, screens.TRAINING.infantry_tab)
        actions.tap(instance_id, tab)
        await asyncio.sleep(1.0)

        image = actions.capture_screen_bgr(instance_id)
        queue_result = await ocr.ocr_region(image, screens.TRAINING.queue_slots_region)
        if match(queue_result.text, ["full", "queue full", "max"]):
            logger.info("Training queue full on %s", instance_id)
            return TaskResult(
                success=True,
                next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            )

        # Fill max quantity and train
        actions.tap(instance_id, screens.TRAINING.max_btn)
        await asyncio.sleep(0.5)
        actions.tap(instance_id, screens.TRAINING.train_btn)
        await asyncio.sleep(1.5)

        logger.info("Training started on %s/%s", instance_id, self.player_id)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
        )
