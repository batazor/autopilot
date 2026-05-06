from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from navigation.detector import ScreenDetector, ScreenName
from navigation.screen_graph import EDGE_TAPS, route_taps

logger = logging.getLogger(__name__)

_MAIN_CITY = ScreenName.MAIN_CITY


class Navigator:
    def __init__(
        self,
        capture_fn: object,
        tap_fn: object,
        *,
        redis_client: Any | None = None,
    ) -> None:
        self._capture = capture_fn   # Callable[[str], np.ndarray]
        self._tap = tap_fn           # Callable[[str, Point], None]
        self._detector = ScreenDetector()
        self._redis = redis_client
        self._area_doc: dict[str, Any] | None = None

    def _load_area_doc(self) -> dict[str, Any]:
        if self._area_doc is not None:
            return self._area_doc
        import json
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        self._area_doc = json.loads((repo_root / "area.json").read_text(encoding="utf-8"))
        return self._area_doc

    def _tap_region_name(self, instance_id: str, region_name: str, *, dev_w: int, dev_h: int) -> bool:
        area_doc = self._load_area_doc()
        pair = screen_region_by_name(area_doc, region_name)
        if pair is None:
            logger.warning("Navigator: unknown region %r in area.json", region_name)
            return False
        _entry, reg = pair
        bbox = reg.get("bbox")
        if not isinstance(bbox, dict):
            logger.warning("Navigator: region %r missing bbox", region_name)
            return False
        pt = bbox_percent_center_to_device_point(bbox, dev_w, dev_h)
        self._tap(instance_id, pt)  # type: ignore[operator]
        return True

    def _state_key(self, instance_id: str) -> str:
        return f"wos:instance:{instance_id}:state"

    async def _write_screen(self, instance_id: str, screen: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset(self._state_key(instance_id), "current_screen", screen)
        except Exception:
            logger.debug("Navigator: failed to write current_screen to Redis", exc_info=True)

    async def navigate_to(self, target: ScreenName, instance_id: str) -> bool:
        for attempt in range(10):
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
            current = await self._detector.detect_screen(image)

            if current == target:
                await self._write_screen(instance_id, str(target))
                return True

            if current == ScreenName.UNKNOWN:
                logger.warning(
                    "Unknown screen on %s attempt %d — backing to main city",
                    instance_id,
                    attempt,
                )
                await self._write_screen(instance_id, "")
                # When screen is unknown, prefer Android BACK (works across screens/popups).
                # If the project has a calibrated UI back button region, we try it first.
                img: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                dev_h, dev_w = int(img.shape[0]), int(img.shape[1])
                if not self._tap_region_name(instance_id, "back_btn", dev_w=dev_w, dev_h=dev_h):
                    # No fallback: do not use Android keyevents; do not guess coordinates.
                    logger.warning(
                        "Unknown screen and no back_btn region; taking no action on %s",
                        instance_id,
                    )
                await asyncio.sleep(1.5)
                continue

            # Try direct BFS route (src → dst).
            hop_sequences = route_taps(str(current), str(target))

            if hop_sequences is None and current != _MAIN_CITY:
                # No direct route or missing taps: go main_city first, then retry.
                to_hub = route_taps(str(current), str(_MAIN_CITY))
                if to_hub:
                    await self._execute_hops(instance_id, to_hub)
                else:
                    # Last resort: blind back tap to escape current screen.
                    logger.warning(
                        "No route %s → main_city on %s; using back_btn",
                        current,
                        instance_id,
                    )
                    img2: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                    dev_h2, dev_w2 = int(img2.shape[0]), int(img2.shape[1])
                    self._tap_region_name(instance_id, "back_btn", dev_w=dev_w2, dev_h=dev_h2)
                    await asyncio.sleep(1.5)
                continue

            if hop_sequences is None:
                # Already at main_city but no path to target.
                from_hub = route_taps(str(_MAIN_CITY), str(target))
                if from_hub:
                    await self._execute_hops(instance_id, from_hub)
                else:
                    logger.error(
                        "No navigation path from %s to %s (and no route via main_city)",
                        current,
                        target,
                    )
                    return False
                continue

            await self._execute_hops(instance_id, hop_sequences)

        logger.error("Failed to navigate to %s after 10 attempts", target)
        await self._write_screen(instance_id, "")
        return False

    async def _execute_hops(
        self, instance_id: str, hop_sequences: list[list[object]]
    ) -> None:
        # Use current framebuffer size for percent->pixel mapping.
        img: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
        dev_h, dev_w = int(img.shape[0]), int(img.shape[1])
        for taps in hop_sequences:
            for point in taps:
                # Tap steps are always region names (strings).
                self._tap_region_name(instance_id, str(point), dev_w=dev_w, dev_h=dev_h)
                await asyncio.sleep(0.8)
            await asyncio.sleep(1.5)
