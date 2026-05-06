from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from actions.tap import BotActions
from capture.window import QuartzCapture
from config.loader import get_settings
from layout import screens
from navigation.detector import ScreenDetector, ScreenName
from navigation.navigator import Navigator
from ocr.client import OcrClient
from ocr.fuzzy import match

logger = logging.getLogger(__name__)


class AccountSwitcher:
    """Switches between in-game player accounts within one BlueStacks instance."""

    def __init__(self, redis_client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client
        self._actions = BotActions()
        self._capture = QuartzCapture()
        self._detector = ScreenDetector()
        self._ocr = OcrClient()
        self._settings = get_settings()

        self._navigator = Navigator(
            capture_fn=self._capture_image,
            tap_fn=self._actions.tap,
        )

    def _capture_image(self, instance_id: str) -> object:
        import numpy as np

        title = self._actions._get_window_title(instance_id)
        wid = self._capture.find_window(title)
        return self._capture.capture(wid)

    async def current_player(self, instance_id: str) -> str | None:
        raw = await self._redis.hget(f"wos:instance:{instance_id}:state", "active_player")
        if raw:
            return raw.decode() if isinstance(raw, bytes) else raw
        return None

    async def switch_to(self, player_id: str, instance_id: str) -> bool:
        current = await self.current_player(instance_id)
        if current == player_id:
            return True

        # Determine slot index for the target player within this instance
        slot_index = self._slot_index(player_id, instance_id)
        if slot_index is None:
            logger.error("Player %s not found on instance %s", player_id, instance_id)
            return False

        logger.info(
            "Switching %s → %s on %s (slot %d)", current, player_id, instance_id, slot_index
        )

        # Navigate to account switcher (accessible from BlueStacks profile menu)
        ok = await self._navigator.navigate_to(ScreenName.ACCOUNT_SWITCHER, instance_id)
        if not ok:
            return False

        slot_point = [
            screens.ACCOUNT_SWITCHER.slot_1,
            screens.ACCOUNT_SWITCHER.slot_2,
            screens.ACCOUNT_SWITCHER.slot_3,
        ][slot_index]

        self._actions.tap(instance_id, slot_point)
        await asyncio.sleep(3.0)

        # Verify the switch succeeded
        import numpy as np

        image = self._capture_image(instance_id)
        result = await self._ocr.ocr_region(
            image,  # type: ignore[arg-type]
            screens.ACCOUNT_SWITCHER.title_region,
        )
        player_cfg = self._settings.players.get(player_id)
        expected_name = player_cfg.name if player_cfg else player_id

        if match(result.text, [expected_name, player_id]):
            await self._redis.hset(
                f"wos:instance:{instance_id}:state", "active_player", player_id
            )
            logger.info("Switched to %s on %s", player_id, instance_id)
            return True

        logger.warning(
            "Switch verification failed: got '%s', expected '%s'",
            result.text,
            expected_name,
        )
        return False

    def _slot_index(self, player_id: str, instance_id: str) -> int | None:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                if player_id in inst.player_ids:
                    return inst.player_ids.index(player_id)
        return None
