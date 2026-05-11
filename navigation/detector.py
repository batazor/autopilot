from __future__ import annotations

import asyncio
import json
import logging
from enum import StrEnum
from pathlib import Path

import numpy as np
from tenacity import RetryError

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.types import Region
from navigation.screen_graph import (
    screen_landmark_rules,
    screen_text_switch_rules,
    screen_verify_screen_names,
)
from ocr.client import OcrClient
from ocr.fuzzy import match

logger = logging.getLogger(__name__)


# ScreenName is generated at import time from screen_verify.yaml plus a small
# list of well-known sentinel/hub screens. Adding a new screen now means
# editing screen_verify.yaml only — no Python change required.
#
# Identifier convention (slug → Python attribute name):
#   * uppercase the value;
#   * replace ``.`` with ``_`` (so ``hero.recrutment`` → ``HERO_RECRUTMENT``);
#   * drop ``-`` and other non-identifier chars (so ``event.7-day`` → ``EVENT_7DAY``).
#
# Anything that legitimately needs a stable Python identifier (constants used
# by ``navigator.py`` / tests) must round-trip through this rule. To audit a
# breaking rename, grep for ``ScreenName.<NAME>``.

_WELL_KNOWN_SCREEN_VALUES: tuple[str, ...] = (
    # Sentinel: detector returns UNKNOWN when no match.
    "unknown",
    # Hub: used as a routing constant by Navigator (``_MAIN_CITY``).
    "main_city",
    # Topology-only screen with no OCR/landmark rules — kept here so callers
    # still get ``ScreenName.SUGGESTION_BOX``.
    "suggestion_box",
)


def _value_to_py_ident(value: str) -> str:
    out = value.upper().replace(".", "_").replace("-", "")
    # Strip any remaining non-identifier characters defensively (apostrophes,
    # ampersands etc. — slugs should already be clean, but YAML might drift).
    return "".join(ch for ch in out if ch.isalnum() or ch == "_")


def _build_screen_name_enum() -> type[StrEnum]:
    """Compose the ScreenName enum from well-known constants + screen_verify.yaml."""
    members: dict[str, str] = {}
    seen_values: set[str] = set()
    for value in _WELL_KNOWN_SCREEN_VALUES:
        ident = _value_to_py_ident(value)
        if value not in seen_values and ident:
            members[ident] = value
            seen_values.add(value)
    for value in screen_verify_screen_names():
        if value in seen_values:
            continue
        ident = _value_to_py_ident(value)
        if not ident or ident in members:
            continue
        members[ident] = value
        seen_values.add(value)
    return StrEnum("ScreenName", members)


ScreenName: type[StrEnum] = _build_screen_name_enum()


class ScreenDetector:
    def __init__(self) -> None:
        self._client = OcrClient()
        self._area_doc: dict[str, object] | None = None

    def _load_area_doc(self) -> dict[str, object]:
        if self._area_doc is not None:
            return self._area_doc
        repo_root = Path(__file__).resolve().parent.parent
        self._area_doc = json.loads((repo_root / "area.json").read_text(encoding="utf-8"))
        return self._area_doc

    @staticmethod
    def _rule_candidates(rule: dict[str, object]) -> list[str]:
        contains = rule.get("contains")
        if isinstance(contains, str):
            return [contains]
        if isinstance(contains, list):
            return [str(x).strip() for x in contains if str(x).strip()]
        return []

    def _landmark_regions(self) -> tuple[list[Region], list[tuple[ScreenName, list[str], float]]]:
        area_doc = self._load_area_doc()
        all_regions: list[Region] = []
        region_map: list[tuple[ScreenName, list[str], float]] = []
        for screen_s in screen_verify_screen_names():
            try:
                screen_name = ScreenName(screen_s)
            except ValueError:
                continue
            for rule in screen_landmark_rules(screen_s):
                region_name = str(rule.get("ocr") or "").strip()
                if not region_name:
                    continue
                pair = screen_region_by_name(area_doc, region_name)
                if pair is None or not isinstance(pair[1].get("bbox"), dict):
                    logger.warning(
                        "ScreenDetector: landmark region %r not found for %s",
                        region_name,
                        screen_s,
                    )
                    continue
                bbox = pair[1]["bbox"]
                try:
                    all_regions.append(
                        Region(
                            int(round(float(bbox["x"]))),
                            int(round(float(bbox["y"]))),
                            int(round(float(bbox["width"]))),
                            int(round(float(bbox["height"]))),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                threshold_raw = rule.get("threshold")
                try:
                    threshold = float(threshold_raw) if threshold_raw is not None else 0.8
                except (TypeError, ValueError):
                    threshold = 0.8
                region_map.append((screen_name, self._rule_candidates(rule), threshold))
        return all_regions, region_map

    async def _detect_by_match_landmarks(self, image: np.ndarray) -> ScreenName:
        rules: list[dict[str, object]] = []
        rule_screens: list[ScreenName] = []
        for screen_s in screen_verify_screen_names():
            try:
                screen_name = ScreenName(screen_s)
            except ValueError:
                continue
            for rule in screen_landmark_rules(screen_s):
                region_name = str(rule.get("match") or "").strip()
                if not region_name:
                    continue
                overlay_rule: dict[str, object] = {
                    "name": f"screen_detector.{screen_s}.{region_name}",
                    "action": "findIcon",
                    "region": region_name,
                    "threshold": rule.get("threshold", 0.9),
                }
                min_sat = rule.get("min_match_saturation")
                if min_sat is not None:
                    overlay_rule["min_match_saturation"] = min_sat
                rules.append(overlay_rule)
                rule_screens.append(screen_name)
        if not rules:
            return ScreenName.UNKNOWN
        try:
            out = await evaluate_overlay_rules_async(
                image,
                self._load_area_doc(),
                Path(__file__).resolve().parent.parent,
                rules,
            )
        except Exception:
            logger.debug("ScreenDetector: match landmarks failed", exc_info=True)
            return ScreenName.UNKNOWN
        for rule, screen_name in zip(rules, rule_screens, strict=False):
            row = out.get(str(rule["name"]))
            if isinstance(row, dict) and row.get("matched"):
                return screen_name
        return ScreenName.UNKNOWN

    def _percent_region_for_name(self, region_name: str) -> Region | None:
        pair = screen_region_by_name(self._load_area_doc(), region_name)
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning("ScreenDetector: text switch region %r not found", region_name)
            return None
        bbox = pair[1]["bbox"]
        try:
            return Region(
                int(round(float(bbox["x"]))),
                int(round(float(bbox["y"]))),
                int(round(float(bbox["width"]))),
                int(round(float(bbox["height"]))),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _to_pixel_region(region: Region, *, width: int, height: int) -> Region:
        return Region(
            int(round(float(region.x) / 100.0 * width)),
            int(round(float(region.y) / 100.0 * height)),
            int(round(float(region.w) / 100.0 * width)),
            int(round(float(region.h) / 100.0 * height)),
        )

    async def _detect_by_text_switch(self, image: np.ndarray) -> ScreenName:
        switch_rules = screen_text_switch_rules()
        if not switch_rules:
            return ScreenName.UNKNOWN
        h, w = int(image.shape[0]), int(image.shape[1])
        regions: list[Region] = []
        rule_map: list[dict[str, object]] = []
        for rule in switch_rules:
            region = self._percent_region_for_name(str(rule.get("ocr") or ""))
            if region is None:
                continue
            regions.append(self._to_pixel_region(region, width=w, height=h))
            rule_map.append(rule)
        if not regions:
            return ScreenName.UNKNOWN
        try:
            results = await self._client.ocr_regions(image, regions)
        except RetryError as exc:
            root = exc.last_attempt.exception() if exc.last_attempt else exc
            logger.error("OCR failed during screen text switch: %s", root, exc_info=True)
            return ScreenName.UNKNOWN
        except Exception:
            logger.exception("OCR failed during screen text switch")
            return ScreenName.UNKNOWN

        for result, rule in zip(results, rule_map, strict=False):
            conf_raw = rule.get("confidence")
            try:
                min_conf = float(conf_raw) if conf_raw is not None else 0.0
            except (TypeError, ValueError):
                min_conf = 0.0
            if float(result.confidence or 0.0) < min_conf:
                continue
            threshold_raw = rule.get("threshold")
            try:
                threshold = float(threshold_raw) if threshold_raw is not None else 0.8
            except (TypeError, ValueError):
                threshold = 0.8
            cases = rule.get("cases")
            if not isinstance(cases, dict):
                continue
            text_lc = str(result.text or "").strip().lower()
            for screen_s, candidates_raw in cases.items():
                try:
                    screen = ScreenName(str(screen_s))
                except ValueError:
                    continue
                candidates = (
                    [str(x).strip() for x in candidates_raw if str(x).strip()]
                    if isinstance(candidates_raw, list)
                    else []
                )
                if not candidates:
                    continue
                # Substring contains first — mirrors ``Navigator._verify_ocr_rule``
                # so a candidate ``squad`` finds itself inside ``"Settings Squad"``
                # without being rejected by ``fuzz.ratio``'s length penalty.
                if any(c.lower() in text_lc for c in candidates):
                    return screen
                # Fuzzy fallback for OCR noise / case-mangled titles where the
                # candidate is approximately the entire title.
                if match(result.text, candidates, threshold=threshold):
                    return screen
        return ScreenName.UNKNOWN

    async def detect_screen(self, image: np.ndarray) -> ScreenName:
        switched = await self._detect_by_text_switch(image)
        if switched != ScreenName.UNKNOWN:
            return switched

        matched = await self._detect_by_match_landmarks(image)
        if matched != ScreenName.UNKNOWN:
            return matched

        percent_regions, region_map = self._landmark_regions()
        if not percent_regions:
            return ScreenName.UNKNOWN
        h, w = int(image.shape[0]), int(image.shape[1])
        all_regions = [self._to_pixel_region(r, width=w, height=h) for r in percent_regions]

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
            screen_name, candidates, threshold = region_map[i]
            if match(result.text, candidates, threshold=threshold):
                scores[screen_name] += 1

        best = max(scores, key=lambda s: scores[s])
        if scores[best] > 0:
            return best
        return ScreenName.UNKNOWN


def suggest_node_for_image_sync(image_bgr: np.ndarray) -> str | None:
    """Best-effort node id for ``image_bgr`` — UI-friendly sync wrapper.

    Tries the full :class:`ScreenDetector` pipeline first (text switch + template
    landmarks + OCR landmarks) so that screens already labeled in
    ``navigation/screen_verify.yaml`` are picked up regardless of whether they
    rely on icons or page titles. When OCR is unavailable (no backend running,
    network error, slow timeout) the function silently falls back to the
    template-only landmark path so the labeling UI stays usable offline.

    Returns the screen id string (e.g. ``"main_city"``) on success, or ``None``
    when no rule fires / detection is unsafe to suggest.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return None
    detector = ScreenDetector()
    try:
        result = asyncio.run(detector.detect_screen(image_bgr))
    except Exception:
        logger.debug("suggest_node_for_image_sync: full detect_screen failed", exc_info=True)
        try:
            result = asyncio.run(detector._detect_by_match_landmarks(image_bgr))
        except Exception:
            logger.debug(
                "suggest_node_for_image_sync: template-only fallback failed", exc_info=True
            )
            return None
    if not isinstance(result, ScreenName) or result == ScreenName.UNKNOWN:
        return None
    return str(result.value)
