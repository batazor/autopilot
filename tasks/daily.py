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
class DailyCheckinTask:
    task_id: str
    player_id: str
    priority: int = 900
    cooldown_seconds: int = 86400
    is_cooperative: bool = False
    task_type: str = field(default="daily_checkin", init=False)

    def estimate_duration(self) -> int:
        return 60

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        capture = QuartzCapture()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=lambda iid: capture.capture(capture.find_window(actions._get_window_title(iid))),
            tap_fn=actions.tap,
        )

        ok = await navigator.navigate_to(ScreenName.MAIN_CITY, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
            )

        actions.tap(instance_id, screens.MAIN_CITY.daily_tasks_btn)
        await asyncio.sleep(2.0)

        image = capture.capture(capture.find_window(actions._get_window_title(instance_id)))
        result = await ocr.ocr_region(image, screens.MAIN_CITY.city_name_region)
        collected = match(result.text, ["collect", "claim", "reward"])

        if collected:
            actions.tap(instance_id, screens.MAIN_CITY.daily_tasks_btn)
            await asyncio.sleep(1.5)

        # Return to main city
        actions.tap(instance_id, screens.MAIN_CITY.back_btn)
        await asyncio.sleep(1.0)

        logger.info("Daily checkin done on %s/%s", instance_id, self.player_id)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=timezone.utc) + timedelta(seconds=self.cooldown_seconds),
        )
