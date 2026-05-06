from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from actions.tap import BotActions
from analysis.overlay import run_overlay_analysis
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class MailGiftCheckTask:
    """If currently on mail screen, tap the gift icon when present."""

    task_id: str
    player_id: str
    priority: int = 500
    cooldown_seconds: int = 15
    is_cooperative: bool = False
    task_type: str = field(default="mail_gift_check", init=False)

    def estimate_duration(self) -> int:
        return 5

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        repo_root = Path(__file__).resolve().parent.parent

        image_bgr = actions.capture_screen_bgr(instance_id)
        overlay = await run_overlay_analysis(image_bgr, repo_root=repo_root, current_screen="mail")

        on_mail = overlay.get("mail_page_back.visible")
        gift = overlay.get("mail_gift.visible")
        if not (isinstance(on_mail, dict) and on_mail.get("matched")):
            return TaskResult(success=True, next_run_at=None, metadata={"reason": "not_on_mail"})

        if isinstance(gift, dict) and gift.get("matched"):
            tx = gift.get("tap_x_pct")
            ty = gift.get("tap_y_pct")
            if tx is not None and ty is not None:
                actions.tap_percent(instance_id, float(tx), float(ty))
                await asyncio.sleep(0.7)
                logger.info("Mail gift tapped on %s/%s", instance_id, self.player_id)
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

