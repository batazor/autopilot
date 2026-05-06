from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from actions.tap import BotActions
from capture.window import QuartzCapture
from layout import screens
from navigation.detector import ScreenDetector, ScreenName
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
    task_type: str = field(default="defend_ally", init=False)

    def estimate_duration(self) -> int:
        return 30

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        capture = QuartzCapture()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=lambda iid: capture.capture(capture.find_window(actions._get_window_title(iid))),
            tap_fn=actions.tap,
        )

        ok = await navigator.navigate_to(ScreenName.ALLIANCE, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            )

        actions.tap(instance_id, screens.ALLIANCE.members_tab)
        await asyncio.sleep(1.5)

        image = capture.capture(capture.find_window(actions._get_window_title(instance_id)))
        alert_result = await ocr.ocr_region(image, screens.ALLIANCE.attack_alerts_region)

        ally_under_attack = match(
            alert_result.text, ["under attack", "attacked", "help", "reinforce"]
        )

        if not ally_under_attack:
            logger.debug("No ally under attack on %s", instance_id)
            return TaskResult(
                success=True,
                next_run_at=datetime.now(tz=timezone.utc) + timedelta(seconds=self.cooldown_seconds),
                metadata={"action": "none"},
            )

        actions.tap(instance_id, screens.ALLIANCE.help_btn)
        await asyncio.sleep(1.5)

        logger.info("Defended ally on %s/%s", instance_id, self.player_id)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=timezone.utc) + timedelta(seconds=self.cooldown_seconds),
            metadata={"action": "defended"},
        )
