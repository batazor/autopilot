from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from actions.tap import BotActions
from analysis.overlay import run_overlay_analysis
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class PageDetectTask:
    """Detect the current page from non-click overlay rules and persist current_screen."""

    task_id: str
    player_id: str
    priority: int = 90_000
    cooldown_seconds: int = 5
    is_cooperative: bool = False
    task_type: str = field(default="page_detect", init=False)
    redis_client: Any | None = None
    skip_fsm: bool = field(default=True, init=False)
    skip_account_check: bool = field(default=True, init=False)

    def estimate_duration(self) -> int:
        return 5

    def _state_key(self, instance_id: str) -> str:
        return f"wos:instance:{instance_id}:state"

    async def execute(self, instance_id: str) -> TaskResult:
        actions = BotActions()
        repo_root = Path(__file__).resolve().parent.parent
        image_bgr = actions.capture_screen_bgr(instance_id)
        overlay = await run_overlay_analysis(image_bgr, repo_root=repo_root, current_screen=None)

        detected: str | None = None
        for payload in overlay.values():
            if not isinstance(payload, dict):
                continue
            if not payload.get("matched"):
                continue
            if payload.get("enqueue_tap", True):
                continue
            sn = str(payload.get("set_node") or "").strip()
            if sn:
                detected = sn

        if not detected:
            logger.info("page_detect: no page detected on %s", instance_id)
            return TaskResult(success=False, next_run_at=None, metadata={"reason": "no_match"})

        if self.redis_client is not None:
            await self.redis_client.hset(self._state_key(instance_id), "current_screen", detected)

        logger.info("page_detect: %s current_screen=%s", instance_id, detected)
        return TaskResult(success=True, next_run_at=None, metadata={"current_screen": detected})
