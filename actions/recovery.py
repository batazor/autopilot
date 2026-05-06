from __future__ import annotations

import asyncio
import logging

import numpy as np

from actions.tap import BotActions
from layout.types import Region
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient
from ocr.fuzzy import match

logger = logging.getLogger(__name__)

_POPUP_CLOSE_KEYWORDS = ["close", "cancel", "ok", "x", "skip", "later"]


class RecoveryHandler:
    def __init__(self) -> None:
        self._actions = BotActions()
        self._detector = ScreenDetector()
        self._ocr = OcrClient()

    def _capture_image(self, instance_id: str) -> np.ndarray:
        return self._actions.capture_screen_bgr(instance_id)

    async def recover_to_main(self, instance_id: str) -> bool:
        for _ in range(5):
            # No-op: do not press phone BACK; do not guess UI coords here.
            await asyncio.sleep(0.8)

        await asyncio.sleep(1.5)
        image = self._capture_image(instance_id)
        screen = await self._detector.detect_screen(image)
        success = screen == ScreenName.MAIN_CITY
        if not success:
            logger.warning("recover_to_main failed on %s, screen=%s", instance_id, screen)
        return success

    async def restart_game(self, instance_id: str) -> bool:
        logger.warning("Restarting game on %s", instance_id)
        self._actions.restart_application(instance_id)

        deadline = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            try:
                image = self._capture_image(instance_id)
                screen = await self._detector.detect_screen(image)
                if screen == ScreenName.MAIN_CITY:
                    logger.info("Game restarted successfully on %s", instance_id)
                    return True
            except Exception:
                pass

        logger.error("Game restart timed out on %s", instance_id)
        return False

    async def handle_popups(self, image: np.ndarray, instance_id: str) -> bool:
        popup_regions = [
            Region(560, 180, 120, 60),
            Region(560, 300, 120, 60),
            Region(300, 900, 160, 60),
        ]
        results = await self._ocr.ocr_regions(image, popup_regions)
        dismissed = False
        for i, result in enumerate(results):
            if match(result.text, _POPUP_CLOSE_KEYWORDS):
                self._actions.tap_region(instance_id, popup_regions[i])
                await asyncio.sleep(0.5)
                dismissed = True
        return dismissed
