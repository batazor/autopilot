from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from actions.tap import BotActions
from analysis.overlay import run_overlay_analysis
from navigation.navigator import Navigator
from navigation.detector import ScreenName
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class MainCityCheckTask:
    """Low-cost idle task: ensure we're on main city and check for obvious notifications."""

    task_id: str
    player_id: str
    priority: int = 1
    cooldown_seconds: int = 120
    is_cooperative: bool = False
    task_type: str = field(default="main_city_check", init=False)
    redis_client: Any | None = None

    def estimate_duration(self) -> int:
        return 30

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()

        nav = Navigator(
            actions.capture_screen_bgr,
            actions.tap,
            redis_client=self.redis_client,
        )
        ok = await nav.navigate_to(ScreenName.MAIN_CITY, instance_id)
        if not ok:
            return TaskResult(
                success=False,
                next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
                metadata={"reason": "navigate_failed"},
            )

        repo_root = Path(__file__).resolve().parent.parent
        image_bgr = actions.capture_screen_bgr(instance_id)
        overlay = await run_overlay_analysis(image_bgr, repo_root=repo_root, current_screen="main_city")

        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
        )

