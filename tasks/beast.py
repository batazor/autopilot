from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from actions.tap import BotActions
from layout import screens
from navigation.detector import ScreenName
from navigation.navigator import Navigator
from ocr.client import OcrClient
from ocr.fuzzy import match
from tasks.base import TaskResult

logger = logging.getLogger(__name__)

_BEAST_BUTTON_REGION_KEYWORDS = ["beast", "wild", "hunt", "attack"]


@dataclass
class BeastTask:
    task_id: str
    player_id: str
    priority: int = 700
    cooldown_seconds: int = 3600
    is_cooperative: bool = True
    task_type: str = field(default="beast", init=False)

    def estimate_duration(self) -> int:
        return 180

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=actions.capture_screen_bgr,
            tap_fn=actions.tap,
        )

        ok = await navigator.navigate_to(ScreenName.ALLIANCE, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=UTC) + timedelta(minutes=30),
            )

        image = actions.capture_screen_bgr(instance_id)
        result = await ocr.ocr_region(image, screens.ALLIANCE.attack_alerts_region)
        beast_available = match(result.text, _BEAST_BUTTON_REGION_KEYWORDS)

        if not beast_available:
            logger.debug("No beast available on %s", instance_id)
            return TaskResult(
                success=True,
                next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
                metadata={"action": "none"},
            )

        # Tap beast rally button
        actions.tap(instance_id, screens.ALLIANCE.attack_alerts_region.center())
        await asyncio.sleep(2.0)

        image = actions.capture_screen_bgr(instance_id)
        confirm_result = await ocr.ocr_region(image, screens.ALLIANCE.attack_alerts_region)
        if match(confirm_result.text, ["confirm", "join", "attack"]):
            actions.tap(instance_id, screens.ALLIANCE.help_btn)
            await asyncio.sleep(1.5)

        logger.info("Beast hunt joined on %s/%s", instance_id, self.player_id)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            metadata={"action": "joined"},
        )
