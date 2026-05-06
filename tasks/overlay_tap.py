"""Tap the center of an ``area.json`` region — used after template overlay rules match."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from actions.tap import BotActions
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.types import Point
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class OverlayTapTask:
    task_id: str
    player_id: str
    priority: int = 50_000
    cooldown_seconds: int = 1
    is_cooperative: bool = False
    task_type: str = field(default="overlay_tap", init=False)
    region_name: str = ""
    tap_x_pct: float | None = None  # % of frame; set when overlay used ``search_region``
    tap_y_pct: float | None = None
    threshold: float | None = None
    skip_fsm: bool = field(default=True, init=False)
    skip_account_check: bool = field(default=True, init=False)

    def estimate_duration(self) -> int:
        return 15

    async def execute(self, instance_id: str) -> TaskResult:
        key = str(self.region_name or "").strip()
        if not key:
            logger.warning("overlay_tap missing region_name")
            return TaskResult(success=False, next_run_at=None)

        repo_root = Path(__file__).resolve().parent.parent
        area_path = repo_root / "area.json"
        if not area_path.is_file():
            logger.error("area.json missing at %s", area_path)
            return TaskResult(success=False, next_run_at=None)

        area_doc = json.loads(area_path.read_text(encoding="utf-8"))
        pair = screen_region_by_name(area_doc, key)
        if pair is None:
            logger.warning("overlay_tap unknown region %r", key)
            return TaskResult(success=False, next_run_at=None)

        _screen, reg = pair
        bbox = reg.get("bbox")
        if not isinstance(bbox, dict):
            logger.warning("overlay_tap region %r has no bbox", key)
            return TaskResult(success=False, next_run_at=None)

        actions = BotActions()
        dev_w, dev_h = actions.screen_resolution(instance_id)
        tx, ty = self.tap_x_pct, self.tap_y_pct
        if tx is not None and ty is not None:
            point = Point(
                int(round(tx / 100.0 * dev_w)),
                int(round(ty / 100.0 * dev_h)),
            )
        else:
            point = bbox_percent_center_to_device_point(bbox, dev_w, dev_h)

        logger.info(
            "overlay_tap %s region=%s → (%d,%d) on %s",
            self.task_id,
            key,
            point.x,
            point.y,
            instance_id,
        )
        actions.tap(instance_id, point)
        await asyncio.sleep(0.35)
        return TaskResult(success=True, next_run_at=None)
