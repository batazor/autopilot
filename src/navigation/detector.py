from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

import cv2
import numpy as np
from tenacity import RetryError

from analysis.overlay_engine import evaluate_overlay_rules_async
from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.types import Region
from navigation.screen_graph import (
    screen_landmark_rules,
    screen_verify_config_fingerprint,
    screen_verify_modal_preempt_names,
    screen_verify_order_names,
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


# ScreenName is composed at runtime from screen_verify.yaml so its members
# are dynamic — ty can't statically see e.g. ``ScreenName.UNKNOWN`` on the
# resulting ``type[StrEnum]`` object, and ``type[StrEnum]`` itself is not
# accepted in type-annotation position. Under TYPE_CHECKING expose a plain
# ``StrEnum`` subclass with the three well-known members so static analysis
# sees a normal enum class; at runtime the dynamic factory still wins.
if TYPE_CHECKING:
    import numpy as np
    class ScreenName(StrEnum):
        # Sentinels + hubs (always present via ``_WELL_KNOWN_SCREEN_VALUES``).
        UNKNOWN = "unknown"
        MAIN_CITY = "main_city"
        SUGGESTION_BOX = "suggestion_box"
        # Other landmarks referenced by name from Python — discovered via
        # ``grep ScreenName\\.``. Values must match the slug used in
        # ``screen_verify.yaml``. Adding a new ``ScreenName.<X>`` site means
        # appending the matching member here so ty stops flagging it; the
        # runtime factory composes the same member from the YAML.
        ARENA = "arena"
        BUILDING = "building"
        CHIEF_PROFILE = "chief_profile"
        LOADING = "loading"
        MAIL = "mail"
        MAIL_SYSTEM = "mail_system"
        RECONNECT = "reconnect"
        WELCOME_BACK = "welcome_back"
else:
    ScreenName = _build_screen_name_enum()


class ScreenDetector:
    _landmark_rules_cache_fp: ClassVar[tuple[Any, ...] | None] = None
    _landmark_rules_cache: ClassVar[
        dict[tuple[str, str], tuple[list[dict[str, Any]], list[list[str]]]]
    ] = {}

    def __init__(self, ocr_client: OcrClient) -> None:
        self._client = ocr_client
        self._area_doc: dict[str, object] | None = None
        # Set to True by ``detect_screen`` when the sticky path confirmed the
        # caller's ``hint``; reset to False when the full pipeline runs. Used
        # by tests + the worker's logs to distinguish "verified what we
        # thought we were on" from "did the global scan".
        self.last_used_sticky_verify: bool = False

    @classmethod
    def _landmark_overlay_rules_cached(
        cls,
        screen_s: str,
        *,
        name_prefix: str,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        fp = screen_verify_config_fingerprint()
        if fp != cls._landmark_rules_cache_fp:
            cls._landmark_rules_cache.clear()
            cls._landmark_rules_cache_fp = fp
        key = (screen_s, name_prefix)
        cached = cls._landmark_rules_cache.get(key)
        if cached is None:
            cached = cls._landmark_overlay_rules_for_screen(screen_s, name_prefix=name_prefix)
            cls._landmark_rules_cache[key] = cached
        return cached

    def _load_area_doc(self) -> dict[str, object]:
        if self._area_doc is not None:
            return self._area_doc
        root = repo_root()
        self._area_doc = load_area_doc(root)
        return self._area_doc

    @staticmethod
    def _rule_candidates(rule: dict[str, object]) -> list[str]:
        contains = rule.get("contains")
        if isinstance(contains, str):
            return [contains]
        if isinstance(contains, list):
            return [str(x).strip() for x in contains if str(x).strip()]
        return []

    def _landmark_regions(self) -> tuple[list[Region], list[tuple[ScreenName, list[str], float, str]]]:
        area_doc = self._load_area_doc()
        all_regions: list[Region] = []
        region_map: list[tuple[ScreenName, list[str], float, str]] = []
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
                region_map.append(
                    (screen_name, self._rule_candidates(rule), threshold, region_name)
                )
        return all_regions, region_map

    @staticmethod
    def _landmark_overlay_rules_for_screen(
        screen_s: str,
        *,
        name_prefix: str,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        """Compile template/tab landmark rules for one screen (detection or verify)."""
        rules: list[dict[str, Any]] = []
        groups: list[list[str]] = []
        for rule in screen_landmark_rules(screen_s):
            region_name = str(rule.get("match") or "").strip()
            tab_region_name = str(rule.get("tab_active") or "").strip()
            if not region_name and not tab_region_name:
                continue
            group_names: list[str] = []
            if region_name:
                overlay_rule: dict[str, Any] = {
                    "name": f"{name_prefix}.{screen_s}.{region_name}",
                    "action": "findIcon",
                    "region": region_name,
                    "threshold": rule.get("threshold", 0.9),
                }
                min_sat = rule.get("min_match_saturation")
                if min_sat is not None:
                    overlay_rule["min_match_saturation"] = min_sat
                rules.append(overlay_rule)
                group_names.append(str(overlay_rule["name"]))
            if tab_region_name:
                overlay_rule = {
                    "name": f"{name_prefix}.{screen_s}.{tab_region_name}.active",
                    "region": tab_region_name,
                    "isTabActive": True,
                }
                rules.append(overlay_rule)
                group_names.append(str(overlay_rule["name"]))
            if group_names:
                groups.append(group_names)
        return rules, groups

    @staticmethod
    def _first_matching_landmark_group(
        out: dict[str, Any],
        groups: list[list[str]],
    ) -> bool:
        for group_names in groups:
            if all(
                isinstance(out.get(rule_name), dict) and out[rule_name].get("matched")
                for rule_name in group_names
            ):
                return True
        return False

    @staticmethod
    def _merge_screen_probe_order(
        ordered: list[str],
        *,
        try_first: list[str] | None,
    ) -> list[str]:
        """Prepend ``try_first`` (routing expectation) without duplicates."""
        if not try_first:
            return ordered
        front: list[str] = []
        seen: set[str] = set()
        for raw in try_first:
            name = str(raw).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            front.append(name)
        return front + [s for s in ordered if s not in seen]

    async def _detect_by_match_landmarks(
        self,
        image: np.ndarray,
        *,
        screen_names: list[str] | None = None,
        try_first: list[str] | None = None,
        frame_gray: np.ndarray | None = None,
    ) -> ScreenName:
        """Template/tab landmark scan in ``screen_verify`` priority order.

        Evaluates one screen at a time and stops at the first landmark group
        that fully matches — same winner as the historical single-batch scan,
        without running every screen's ``findIcon`` rules on every frame.

        ``try_first``: screens to probe before the priority list (e.g. the
        hop destination the navigator just tapped toward).
        """
        ordered = (
            screen_verify_screen_names()
            if screen_names is None
            else screen_verify_order_names(screen_names)
        )
        ordered = self._merge_screen_probe_order(ordered, try_first=try_first)
        area_doc = self._load_area_doc()
        root = repo_root()
        for screen_s in ordered:
            try:
                screen_name = ScreenName(screen_s)
            except ValueError:
                continue
            rules, groups = self._landmark_overlay_rules_cached(
                screen_s,
                name_prefix="screen_detector",
            )
            if not rules:
                continue
            try:
                out = await evaluate_overlay_rules_async(
                    image,
                    area_doc,
                    root,
                    rules,
                    frame_gray=frame_gray,
                )
            except Exception:
                logger.debug("ScreenDetector: match landmarks failed", exc_info=True)
                return ScreenName.UNKNOWN
            if self._first_matching_landmark_group(out, groups):
                return screen_name
        return ScreenName.UNKNOWN

    @staticmethod
    def _sticky_preempt_candidates(hint_name: ScreenName) -> list[str]:
        """Screens that should get a chance to override a verified sticky hint.

        Sticky verification keeps steady-state detection cheap, but modal screens
        can replace each other without first invalidating the old landmark. Check
        earlier screen-verify entries before returning the old hint; exclude
        ``main_city`` because it often remains visible underneath overlays.
        """
        out: list[str] = []
        for screen_s in screen_verify_screen_names():
            if screen_s == str(hint_name):
                break
            try:
                candidate = ScreenName(screen_s)
            except ValueError:
                continue
            if candidate in (ScreenName.UNKNOWN, ScreenName.MAIN_CITY):
                continue
            out.append(screen_s)
        return out

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

    async def _verify_screen(
        self,
        image: np.ndarray,
        screen: ScreenName,
        *,
        frame_gray: np.ndarray | None = None,
    ) -> bool:
        """Sticky check: does this frame still satisfy ``screen``'s own rules?

        Runs ONLY the rules attached to ``screen`` in
        ``navigation/screen_verify.yaml`` — template landmarks first
        (in-process ``cv2.matchTemplate``), then OCR landmarks only for
        legacy/test configs. Short-circuits at the first hit.

        Big win on the steady-state case where the bot dwells on one screen
        for many ticks: skips the full multi-screen template scan.
        """
        name_s = str(screen)
        overlay_rules, overlay_rule_groups = self._landmark_overlay_rules_cached(
            name_s,
            name_prefix="screen_detector.verify",
        )
        ocr_landmarks = [
            rule
            for rule in screen_landmark_rules(name_s)
            if str(rule.get("ocr") or "").strip()
            and not (
                str(rule.get("match") or "").strip()
                or str(rule.get("tab_active") or "").strip()
            )
        ]

        if overlay_rules:
            try:
                out = await evaluate_overlay_rules_async(
                    image,
                    self._load_area_doc(),
                    repo_root(),
                    overlay_rules,
                    frame_gray=frame_gray,
                )
            except Exception:
                logger.debug(
                    "ScreenDetector._verify_screen: overlay landmarks failed",
                    exc_info=True,
                )
                out = {}
            if self._first_matching_landmark_group(out, overlay_rule_groups):
                return True

        if ocr_landmarks:
            h, w = int(image.shape[0]), int(image.shape[1])
            regions = []
            region_ids = []
            rules_used = []
            for rule in ocr_landmarks:
                region_name = str(rule.get("ocr") or "").strip()
                if not region_name:
                    continue
                region = self._percent_region_for_name(region_name)
                if region is None:
                    continue
                regions.append(self._to_pixel_region(region, width=w, height=h))
                region_ids.append(region_name)
                rules_used.append(rule)
            if regions:
                try:
                    results = await self._client.ocr_regions(
                        image, regions, region_ids=region_ids
                    )
                except (RetryError, Exception):
                    logger.debug(
                        "ScreenDetector._verify_screen: OCR landmarks failed",
                        exc_info=True,
                    )
                    results = []
                for result, rule in zip(results, rules_used, strict=False):
                    candidates = self._rule_candidates(rule)
                    if not candidates:
                        continue
                    threshold_raw = rule.get("threshold")
                    try:
                        threshold = (
                            float(threshold_raw) if threshold_raw is not None else 0.8
                        )
                    except (TypeError, ValueError):
                        threshold = 0.8
                    if match(result.text, candidates, threshold=threshold):
                        return True

        return False

    @staticmethod
    def _parse_screen_name(value: ScreenName | str | None) -> ScreenName | None:
        if value is None:
            return None
        try:
            name = value if isinstance(value, ScreenName) else ScreenName(str(value))
        except ValueError:
            return None
        if name == ScreenName.UNKNOWN:
            return None
        return name

    async def detect_screen(
        self,
        image: np.ndarray,
        *,
        hint: ScreenName | str | None = None,
        expected: ScreenName | str | None = None,
    ) -> ScreenName:
        """Identify the current screen on ``image``.

        ``hint`` (sticky path): when set, run only the rules attached to
        that screen first; if any of them fires, return ``hint`` without
        touching the other ~100+ rules. Falls through to the full pipeline
        on miss, so the worst case is the historical cost. The caller in
        the worker passes its remembered ``_last_detected_screen`` so a
        bot that dwells on one screen for many ticks avoids the global
        scan every frame.

        ``expected`` (routing path): after a navigation hop, probe this
        screen's landmarks before the global priority scan. The navigator
        passes the hop destination from ``route_hops`` / BFS. On miss,
        detection continues through ``hint`` and the full pipeline — same
        rules, different order.
        """
        self.last_used_sticky_verify = False
        frame_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        expected_name = self._parse_screen_name(expected)
        modal_preempt = screen_verify_modal_preempt_names()
        try_first: list[str] | None = None
        if expected_name is not None:
            try_first = [str(expected_name)]
        if hint:
            hint_name = self._parse_screen_name(hint)
            if hint_name == ScreenName.MAIN_CITY:
                matched = await self._detect_by_match_landmarks(
                    image,
                    screen_names=modal_preempt,
                    try_first=try_first,
                    frame_gray=frame_gray,
                )
                if matched != ScreenName.UNKNOWN:
                    return matched
                matched = await self._detect_by_match_landmarks(
                    image,
                    screen_names=[str(ScreenName.MAIN_CITY)],
                    frame_gray=frame_gray,
                )
                if matched == ScreenName.MAIN_CITY:
                    self.last_used_sticky_verify = True
                    return ScreenName.MAIN_CITY
            elif (
                hint_name is not None
                and hint_name != ScreenName.UNKNOWN
            ):
                try:
                    verified = await self._verify_screen(
                        image, hint_name, frame_gray=frame_gray
                    )
                except Exception:
                    logger.debug(
                        "ScreenDetector: sticky verify for %s raised — falling back",
                        hint_name,
                        exc_info=True,
                    )
                    verified = False
                if verified:
                    candidates = self._sticky_preempt_candidates(hint_name)
                    if candidates:
                        matched = await self._detect_by_match_landmarks(
                            image,
                            screen_names=candidates,
                            frame_gray=frame_gray,
                        )
                        if matched != ScreenName.UNKNOWN:
                            return matched
                    self.last_used_sticky_verify = True
                    return hint_name

        full_try_first = try_first
        if hint and self._parse_screen_name(hint) == ScreenName.MAIN_CITY:
            full_try_first = self._merge_screen_probe_order(
                modal_preempt,
                try_first=try_first,
            )
        matched = await self._detect_by_match_landmarks(
            image,
            try_first=full_try_first,
            frame_gray=frame_gray,
        )
        if matched != ScreenName.UNKNOWN:
            return matched

        percent_regions, region_map = self._landmark_regions()
        if not percent_regions:
            return ScreenName.UNKNOWN
        h, w = int(image.shape[0]), int(image.shape[1])
        all_regions = [self._to_pixel_region(r, width=w, height=h) for r in percent_regions]
        region_ids = [t[3] for t in region_map]

        try:
            results = await self._client.ocr_regions(image, all_regions, region_ids=region_ids)
        except RetryError as exc:
            # Tenacity wraps the root exception; surface the actual cause for faster diagnosis.
            root = exc.last_attempt.exception() if exc.last_attempt else exc
            logger.exception("OCR failed during screen detection: %s", root)
            return ScreenName.UNKNOWN
        except Exception:
            logger.exception("OCR failed during screen detection")
            return ScreenName.UNKNOWN

        # Pair OCR results back to landmarks by region_id, not by position.
        # ``OcrClient.ocr_regions`` filters out ``None`` slots before returning,
        # so a single dropped/error slot would shift every subsequent index and
        # silently score the wrong screen.
        by_rid = {t[3]: t for t in region_map}
        scores: dict[ScreenName, int] = dict.fromkeys(ScreenName, 0)
        for result in results:
            entry = by_rid.get(result.region_id)
            if entry is None:
                continue
            screen_name, candidates, threshold, _region_name = entry
            if match(result.text, candidates, threshold=threshold):
                scores[screen_name] += 1

        best = max(scores, key=lambda s: scores[s])
        if scores[best] > 0:
            return best
        return ScreenName.UNKNOWN


def suggest_node_for_image_sync(image_bgr: np.ndarray) -> str | None:
    """Best-effort node id for ``image_bgr`` — UI-friendly sync wrapper.

    Tries the full :class:`ScreenDetector` pipeline first (template landmarks,
    plus OCR landmarks only for legacy/test configs). When OCR is unavailable
    (no backend running, network error, slow timeout) the function silently
    falls back to the template-only landmark path so the labeling UI stays
    usable offline.

    Returns the screen id string (e.g. ``"main_city"``) on success, or ``None``
    when no rule fires / detection is unsafe to suggest.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return None
    from config.loader import load_settings

    detector = ScreenDetector(OcrClient(load_settings()))
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
