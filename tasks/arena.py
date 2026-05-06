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
class ArenaTask:
    task_id: str
    player_id: str
    priority: int = 500
    cooldown_seconds: int = 10800
    is_cooperative: bool = False
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="arena", init=False)

    def estimate_duration(self) -> int:
        return 120

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        ocr = OcrClient()
        navigator = Navigator(
            capture_fn=actions.capture_screen_bgr,
            tap_fn=actions.tap,
            redis_client=self.redis_client,
        )

        ok = await navigator.navigate_to(ScreenName.ARENA, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=UTC) + timedelta(minutes=15),
            )

        fights_done = 0
        for _ in range(10):
            image = actions.capture_screen_bgr(instance_id)
            ticket_result = await ocr.ocr_region(image, screens.ARENA.tickets_region)

            if not ticket_result.text or not any(c.isdigit() for c in ticket_result.text):
                break

            tickets = int("".join(c for c in ticket_result.text if c.isdigit()) or "0")
            if tickets == 0:
                break

            actions.tap(instance_id, screens.ARENA.fight_btn)
            await asyncio.sleep(5.0)

            image = actions.capture_screen_bgr(instance_id)
            result_ocr = await ocr.ocr_region(image, screens.ARENA.result_region)
            if match(result_ocr.text, ["victory", "defeat", "result"]):
                actions.tap(instance_id, screens.ARENA.close_result_btn)
                await asyncio.sleep(1.5)
                fights_done += 1

        logger.info("Arena: %d fights on %s/%s", fights_done, instance_id, self.player_id)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            metadata={"fights_done": fights_done},
        )
