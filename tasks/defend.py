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
class DefendAllyTask:
    task_id: str
    player_id: str
    priority: int = 800
    cooldown_seconds: int = 600
    is_cooperative: bool = True
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="defend_ally", init=False)

    def estimate_duration(self) -> int:
        return 30

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=actions.capture_screen_bgr,
            tap_fn=actions.tap,
            redis_client=self.redis_client,
        )

        ok = await navigator.navigate_to(ScreenName.ALLIANCE, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=UTC) + timedelta(minutes=5),
            )

        actions.tap(instance_id, screens.ALLIANCE.members_tab)
        await asyncio.sleep(1.5)

        image = actions.capture_screen_bgr(instance_id)
        alert_result = await ocr.ocr_region(image, screens.ALLIANCE.attack_alerts_region)

        ally_under_attack = match(
            alert_result.text, ["under attack", "attacked", "help", "reinforce"]
        )

        if not ally_under_attack:
            logger.debug("No ally under attack on %s", instance_id)
            return TaskResult(
                success=True,
                next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
                metadata={"action": "none"},
            )

        actions.tap(instance_id, screens.ALLIANCE.help_btn)
        await asyncio.sleep(1.5)

        logger.info("Defended ally on %s/%s", instance_id, self.player_id)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            metadata={"action": "defended"},
        )
