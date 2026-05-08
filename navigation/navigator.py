from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.types import Region
from navigation.detector import ScreenDetector, ScreenName
from navigation.screen_graph import (
    route_hops,
    screen_verify_retry,
    screen_verify_rules,
    screen_verify_screen_names,
)
from ocr.client import OcrClient
from ocr.fuzzy import match as fuzzy_match

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
        self._ocr = OcrClient()
        self._redis = redis_client
        self._area_doc: dict[str, Any] | None = None
        self._repo_root = Path(__file__).resolve().parent.parent

    def _load_area_doc(self) -> dict[str, Any]:
        if self._area_doc is not None:
            return self._area_doc

        self._area_doc = json.loads((self._repo_root / "area.json").read_text(encoding="utf-8"))
        return self._area_doc

    def _tap_region_name(
        self,
        instance_id: str,
        region_name: str,
        *,
        dev_w: int,
        dev_h: int,
    ) -> bool:
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

    async def _verify_match_rule(self, image: np.ndarray, rule: dict[str, object]) -> bool:
        region = str(rule.get("match") or "").strip()
        if not region:
            return False
        threshold_raw = rule.get("threshold")
        try:
            threshold = float(threshold_raw) if threshold_raw is not None else 0.9
        except (TypeError, ValueError):
            threshold = 0.9
        overlay_rule: dict[str, Any] = {
            "name": f"navigator.verify.{region}",
            "action": "findIcon",
            "region": region,
            "threshold": threshold,
        }
        min_sat = rule.get("min_match_saturation")
        if min_sat is not None:
            overlay_rule["min_match_saturation"] = min_sat
        try:
            out = await evaluate_overlay_rules_async(
                image,
                self._load_area_doc(),
                self._repo_root,
                [overlay_rule],
            )
        except Exception:
            logger.debug("Navigator: match verify failed for %s", region, exc_info=True)
            return False
        row = out.get(str(overlay_rule["name"]))
        return bool(isinstance(row, dict) and row.get("matched"))

    async def _verify_ocr_rule(self, image: np.ndarray, rule: dict[str, object]) -> bool:
        region = str(rule.get("ocr") or "").strip()
        if not region:
            return False
        pair = screen_region_by_name(self._load_area_doc(), region)
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning("Navigator: OCR verify region %r not found", region)
            return False
        bbox = pair[1]["bbox"]
        h, w = int(image.shape[0]), int(image.shape[1])
        try:
            px = int(round(float(bbox["x"]) / 100.0 * w))
            py = int(round(float(bbox["y"]) / 100.0 * h))
            pw = int(round(float(bbox["width"]) / 100.0 * w))
            ph = int(round(float(bbox["height"]) / 100.0 * h))
        except (KeyError, TypeError, ValueError):
            return False
        if pw <= 0 or ph <= 0:
            return False
        try:
            result = await self._ocr.ocr_region(image, Region(px, py, pw, ph))
        except Exception:
            logger.debug("Navigator: OCR verify failed for %s", region, exc_info=True)
            return False

        conf_raw = rule.get("confidence")
        try:
            min_conf = float(conf_raw) if conf_raw is not None else 0.0
        except (TypeError, ValueError):
            min_conf = 0.0
        if float(result.confidence or 0.0) < min_conf:
            return False

        contains_raw = rule.get("contains")
        if isinstance(contains_raw, str):
            candidates = [contains_raw]
        elif isinstance(contains_raw, list):
            candidates = [str(x).strip() for x in contains_raw if str(x).strip()]
        else:
            candidates = []
        if not candidates:
            return bool(str(result.text or "").strip())

        text = str(result.text or "").strip().lower()
        if any(candidate.lower() in text for candidate in candidates):
            return True
        threshold_raw = rule.get("threshold")
        try:
            threshold = float(threshold_raw) if threshold_raw is not None else 0.8
        except (TypeError, ValueError):
            threshold = 0.8
        return fuzzy_match(text, candidates, threshold=threshold) is not None

    async def _verify_rule(self, image: np.ndarray, rule: dict[str, object]) -> bool:
        if "match" in rule:
            return await self._verify_match_rule(image, rule)
        if "ocr" in rule:
            return await self._verify_ocr_rule(image, rule)
        return False

    async def _wait_for_screen_verified(self, instance_id: str, target: str) -> bool:
        attempts, interval_seconds = screen_verify_retry(target)
        rules = screen_verify_rules(target)
        for attempt in range(1, attempts + 1):
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
            detected = await self._detector.detect_screen(image)
            if str(detected) == target:
                return True
            for rule in rules:
                if await self._verify_rule(image, rule):
                    return True
            logger.debug(
                "Navigator: screen %s not verified on %s attempt %d/%d",
                target,
                instance_id,
                attempt,
                attempts,
            )
            await asyncio.sleep(interval_seconds)
        return False

    async def detect_current_screen(
        self,
        instance_id: str,
        *,
        attempts: int | None = None,
        interval_seconds: float | None = None,
    ) -> str:
        default_attempts, default_interval = screen_verify_retry()
        attempts_i = max(1, int(attempts if attempts is not None else default_attempts))
        interval_f = max(
            0.0,
            float(interval_seconds if interval_seconds is not None else default_interval),
        )
        for attempt in range(1, attempts_i + 1):
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
            detected = await self._detector.detect_screen(image)
            if detected != ScreenName.UNKNOWN:
                await self._write_screen(instance_id, str(detected))
                return str(detected)
            for screen in screen_verify_screen_names():
                for rule in screen_verify_rules(screen):
                    if await self._verify_rule(image, rule):
                        await self._write_screen(instance_id, screen)
                        return screen
            logger.debug(
                "Navigator: current screen not detected on %s attempt %d/%d",
                instance_id,
                attempt,
                attempts_i,
            )
            await asyncio.sleep(interval_f)
        await self._write_screen(instance_id, "")
        return ""

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
                if not self._tap_region_name(instance_id, "back_button", dev_w=dev_w, dev_h=dev_h):
                    # No fallback: do not use Android keyevents; do not guess coordinates.
                    logger.warning(
                        "Unknown screen and no back_button region; taking no action on %s",
                        instance_id,
                    )
                await asyncio.sleep(1.5)
                continue

            # Try direct BFS route (src → dst).
            hop_sequences = route_hops(str(current), str(target))

            if hop_sequences is None and current != _MAIN_CITY:
                # No direct route or missing taps: go main_city first, then retry.
                to_hub = route_hops(str(current), str(_MAIN_CITY))
                if to_hub:
                    await self._execute_hops(instance_id, to_hub)
                else:
                    # Last resort: blind back tap to escape current screen.
                    logger.warning(
                        "No route %s → main_city on %s; using back_button",
                        current,
                        instance_id,
                    )
                    img2: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                    dev_h2, dev_w2 = int(img2.shape[0]), int(img2.shape[1])
                    self._tap_region_name(instance_id, "back_button", dev_w=dev_w2, dev_h=dev_h2)
                    await asyncio.sleep(1.5)
                continue

            if hop_sequences is None:
                # Already at main_city but no path to target.
                from_hub = route_hops(str(_MAIN_CITY), str(target))
                if from_hub:
                    if await self._execute_hops(instance_id, from_hub):
                        return True
                else:
                    logger.error(
                        "No navigation path from %s to %s (and no route via main_city)",
                        current,
                        target,
                    )
                    return False
                continue

            if await self._execute_hops(instance_id, hop_sequences):
                return True

        logger.error("Failed to navigate to %s after 10 attempts", target)
        await self._write_screen(instance_id, "")
        return False

    async def _execute_hops(
        self, instance_id: str, hop_sequences: list[tuple[str, list[object]]]
    ) -> bool:
        # Use current framebuffer size for percent->pixel mapping.
        img: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
        dev_h, dev_w = int(img.shape[0]), int(img.shape[1])
        for dst_screen, taps in hop_sequences:
            for point in taps:
                # Tap steps are always region names (strings).
                self._tap_region_name(instance_id, str(point), dev_w=dev_w, dev_h=dev_h)
                await asyncio.sleep(0.8)
            await asyncio.sleep(1.5)
            if await self._wait_for_screen_verified(instance_id, str(dst_screen)):
                await self._write_screen(instance_id, str(dst_screen))
            else:
                logger.warning(
                    "Navigator: destination %s was not verified on %s",
                    dst_screen,
                    instance_id,
                )
                await self._write_screen(instance_id, "")
                return False
        return True
