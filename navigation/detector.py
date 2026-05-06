from __future__ import annotations

import asyncio
import logging
from enum import StrEnum

import numpy as np
from tenacity import RetryError

from layout import screens
from ocr.client import OcrClient
from ocr.fuzzy import match

logger = logging.getLogger(__name__)


class ScreenName(StrEnum):
    MAIN_CITY = "main_city"
    ARENA = "arena"
    TRAINING = "training"
    GATHERING = "gathering"
    ALLIANCE = "alliance"
    ACCOUNT_SWITCHER = "account_switcher"
    CHIEF_PROFILE = "chief_profile"
    UNKNOWN = "unknown"


_SCREEN_LANDMARKS: dict[ScreenName, list[tuple[object, list[str]]]] = {
    ScreenName.MAIN_CITY: [
        (screens.MAIN_CITY.city_name_region, ["city", "town", "base"]),
    ],
    ScreenName.ARENA: [
        (screens.ARENA.title_region, ["arena", "battle", "fight"]),
    ],
    ScreenName.TRAINING: [
        (screens.TRAINING.title_region, ["training", "troop", "recruit"]),
    ],
    ScreenName.GATHERING: [
        (screens.GATHERING.title_region, ["gather", "resource", "march"]),
    ],
    ScreenName.ALLIANCE: [
        (screens.ALLIANCE.title_region, ["alliance", "guild", "member"]),
    ],
    ScreenName.ACCOUNT_SWITCHER: [
        (screens.ACCOUNT_SWITCHER.title_region, ["account", "switch", "player"]),
    ],
    ScreenName.CHIEF_PROFILE: [
        # OCR keywords are weak here; prefer overlay icon match. Still useful as a hint.
        (screens.CHIEF_PROFILE.title_region, ["chief", "profile"]),
    ],
}


class ScreenDetector:
    def __init__(self) -> None:
        self._client = OcrClient()

    async def detect_screen(self, image: np.ndarray) -> ScreenName:
        from layout.types import Region

        all_regions: list[Region] = []
        region_map: list[tuple[ScreenName, list[str]]] = []

        for screen_name, landmarks in _SCREEN_LANDMARKS.items():
            for region, candidates in landmarks:
                all_regions.append(region)  # type: ignore[arg-type]
                region_map.append((screen_name, candidates))

        try:
            results = await self._client.ocr_regions(image, all_regions)
        except RetryError as exc:
            # Tenacity wraps the root exception; surface the actual cause for faster diagnosis.
            root = exc.last_attempt.exception() if exc.last_attempt else exc
            logger.error("OCR failed during screen detection: %s", root, exc_info=True)
            return ScreenName.UNKNOWN
        except Exception:
            logger.exception("OCR failed during screen detection")
            return ScreenName.UNKNOWN

        scores: dict[ScreenName, int] = {s: 0 for s in ScreenName}
        for i, result in enumerate(results):
            screen_name, candidates = region_map[i]
            if match(result.text, candidates):
                scores[screen_name] += 1

        best = max(scores, key=lambda s: scores[s])
        if scores[best] > 0:
            return best
        return ScreenName.UNKNOWN
