from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from actions.tap import BotActions
from analysis.overlay import run_overlay_analysis
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class IsNewPeopleTask:
    """Handle `isNewPeople` hint on the main city screen (best-effort tap)."""

    task_id: str
    player_id: str
    priority: int = 500
    cooldown_seconds: int = 120
    is_cooperative: bool = False
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="isNewPeople", init=False)

    def estimate_duration(self) -> int:
        return 10

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        repo_root = Path(__file__).resolve().parent.parent

        image_bgr = actions.capture_screen_bgr(instance_id)
        overlay = await run_overlay_analysis(image_bgr, repo_root=repo_root, current_screen="main_city")

        main = overlay.get("main_city.visible")
        if not (isinstance(main, dict) and main.get("matched")):
            return TaskResult(success=True, next_run_at=None, metadata={"reason": "not_on_main_city"})

        hit = overlay.get("isNewPeople.visible")
        if isinstance(hit, dict) and hit.get("matched"):
            tx = hit.get("tap_x_pct")
            ty = hit.get("tap_y_pct")
            if tx is not None and ty is not None:
                actions.tap_percent(instance_id, float(tx), float(ty))
                await asyncio.sleep(0.8)
                logger.info("isNewPeople tapped on %s/%s", instance_id, self.player_id)
                return TaskResult(
                    success=True,
                    next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
                    metadata={"tapped": True},
                )

        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            metadata={"tapped": False},
        )

