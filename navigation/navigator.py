from __future__ import annotations

import asyncio
import logging

import numpy as np

from layout import screens
from layout.types import Point
from navigation.detector import ScreenDetector, ScreenName

logger = logging.getLogger(__name__)

# Navigation graph: from_screen → {to_screen: [tap sequence]}.
# Full FSM topology (Go parity) is in fsm_screen_map.FSM_SCREEN_EDGES.
_NAV_GRAPH: dict[ScreenName, dict[ScreenName, list[Point]]] = {
    ScreenName.MAIN_CITY: {
        ScreenName.ARENA: [screens.MAIN_CITY.arena_btn],
        ScreenName.TRAINING: [screens.MAIN_CITY.training_btn],
        ScreenName.ALLIANCE: [screens.MAIN_CITY.alliance_btn],
        ScreenName.GATHERING: [screens.MAIN_CITY.world_map_btn],
    },
    ScreenName.ARENA: {
        ScreenName.MAIN_CITY: [screens.ARENA.back_btn],
    },
    ScreenName.TRAINING: {
        ScreenName.MAIN_CITY: [screens.TRAINING.back_btn],
    },
    ScreenName.GATHERING: {
        ScreenName.MAIN_CITY: [screens.GATHERING.back_btn],
    },
    ScreenName.ALLIANCE: {
        ScreenName.MAIN_CITY: [screens.ALLIANCE.back_btn],
    },
    ScreenName.ACCOUNT_SWITCHER: {
        ScreenName.MAIN_CITY: [screens.ACCOUNT_SWITCHER.back_btn],
    },
}


class Navigator:
    def __init__(self, capture_fn: object, tap_fn: object) -> None:
        self._capture = capture_fn  # Callable[[str], np.ndarray]
        self._tap = tap_fn  # Callable[[str, Point], None]
        self._detector = ScreenDetector()

    async def navigate_to(self, target: ScreenName, instance_id: str) -> bool:
        for attempt in range(10):
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
            current = await self._detector.detect_screen(image)

            if current == target:
                return True

            if current == ScreenName.UNKNOWN:
                logger.warning(
                    "Unknown screen on %s attempt %d, backing to main city",
                    instance_id,
                    attempt,
                )
                self._tap(instance_id, screens.MAIN_CITY.back_btn)  # type: ignore[operator]
                await asyncio.sleep(1.5)
                continue

            taps = _NAV_GRAPH.get(current, {}).get(target)
            if taps is None:
                # Route via main city
                if current != ScreenName.MAIN_CITY:
                    taps = _NAV_GRAPH.get(current, {}).get(ScreenName.MAIN_CITY)
                    if taps:
                        for point in taps:
                            self._tap(instance_id, point)  # type: ignore[operator]
                            await asyncio.sleep(0.8)
                    await asyncio.sleep(1.5)
                    continue
                taps = _NAV_GRAPH.get(ScreenName.MAIN_CITY, {}).get(target, [])

            if not taps:
                logger.error("No navigation path from %s to %s", current, target)
                return False

            for point in taps:
                self._tap(instance_id, point)  # type: ignore[operator]
                await asyncio.sleep(0.8)

            await asyncio.sleep(1.5)

        logger.error("Failed to navigate to %s after 10 attempts", target)
        return False
