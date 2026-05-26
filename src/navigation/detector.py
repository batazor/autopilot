from __future__ import annotations

import asyncio
import logging
import os
import threading
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

import cv2
import numpy as np

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_rules import normalize_overlay_action
from config.paths import repo_root
from layout.area_manifest import area_manifest_max_mtime, load_area_doc
from navigation.screen_graph import (
    screen_landmark_rules,
    screen_verify_config_fingerprint,
    screen_verify_modal_preempt_names,
    screen_verify_order_names,
    screen_verify_parent,
    screen_verify_screen_names,
)
from ocr.client import OcrClient

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

        Cold-path optimisation: instead of looping screen-by-screen (each call
        spending ~thread+event-loop overhead in ``_evaluate_overlay_rules_in_thread``),
        we collect the *union* of unique ``(action, region, threshold)`` landmark
        rules across all candidate screens and evaluate them in **at most two
        batches**: parent/unparented rules first, then child rules only for
        parents whose anchor group fired. Group resolution then happens locally
        in priority order — preserving the "first-match wins" semantics without
        paying the N×overhead.

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

        entries: list[
            tuple[str, ScreenName, list[dict[str, Any]], list[list[str]], str | None]
        ] = []
        referenced_parents: set[str] = set()
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
            parent_s = screen_verify_parent(screen_s)
            if parent_s:
                referenced_parents.add(parent_s)
            entries.append((screen_s, screen_name, rules, groups, parent_s))

        if not entries:
            return ScreenName.UNKNOWN

        result_by_key: dict[tuple[str, str, float], dict[str, Any] | None] = {}

        # Phase 1: all rules of unparented screens + every referenced parent's
        # own rules. This covers the gating templates the parent gate needs
        # plus everything required to resolve top-level (non-mail-style) screens.
        phase1: dict[tuple[str, str, float], dict[str, Any]] = {}
        for _screen_s, _name, rules, _groups, parent_s in entries:
            if parent_s is not None:
                continue
            for rule in rules:
                phase1.setdefault(_dedup_key(rule), rule)
        for parent_s in referenced_parents:
            parent_rules, _parent_groups = self._landmark_overlay_rules_cached(
                parent_s,
                name_prefix="screen_detector",
            )
            for rule in parent_rules:
                phase1.setdefault(_dedup_key(rule), rule)

        if phase1:
            try:
                out1 = await _evaluate_overlay_rules_in_thread(
                    image,
                    area_doc,
                    root,
                    list(phase1.values()),
                    frame_gray=frame_gray,
                )
            except Exception:
                logger.debug(
                    "ScreenDetector: phase1 landmark batch failed", exc_info=True
                )
                return ScreenName.UNKNOWN
            for key, rule in phase1.items():
                result_by_key[key] = out1.get(str(rule["name"]))

        # Resolve which referenced parents have their anchor group satisfied.
        # Children of negative parents are skipped from phase 2 entirely — the
        # same optimisation the old _parent_gate_negative provided, just done
        # once after the batched evaluation.
        parents_negative: set[str] = set()
        for parent_s in referenced_parents:
            parent_rules, parent_groups = self._landmark_overlay_rules_cached(
                parent_s,
                name_prefix="screen_detector",
            )
            if not parent_rules or not parent_groups:
                # No gating rules — treat as "let children through".
                continue
            out_p: dict[str, Any] = {}
            for rule in parent_rules:
                cached = result_by_key.get(_dedup_key(rule))
                if cached is not None:
                    out_p[str(rule["name"])] = cached
            if not self._first_matching_landmark_group(out_p, parent_groups):
                parents_negative.add(parent_s)

        # Phase 2: child rules whose parent fired. Only rules not already
        # evaluated in phase 1 are added — the dedup key keeps mail.title
        # (shared between parent ``mail`` and child ``mail.wars``) from
        # re-running cv2.matchTemplate.
        phase2: dict[tuple[str, str, float], dict[str, Any]] = {}
        for _screen_s, _name, rules, _groups, parent_s in entries:
            if parent_s is None or parent_s in parents_negative:
                continue
            for rule in rules:
                key = _dedup_key(rule)
                if key in result_by_key:
                    continue
                phase2.setdefault(key, rule)

        if phase2:
            try:
                out2 = await _evaluate_overlay_rules_in_thread(
                    image,
                    area_doc,
                    root,
                    list(phase2.values()),
                    frame_gray=frame_gray,
                )
            except Exception:
                logger.debug(
                    "ScreenDetector: phase2 landmark batch failed", exc_info=True
                )
                return ScreenName.UNKNOWN
            for key, rule in phase2.items():
                result_by_key[key] = out2.get(str(rule["name"]))

        # First-match-wins resolution in priority order.
        for _screen_s, screen_name, rules, groups, parent_s in entries:
            if parent_s is not None and parent_s in parents_negative:
                continue
            out_s: dict[str, Any] = {}
            for rule in rules:
                cached = result_by_key.get(_dedup_key(rule))
                if cached is not None:
                    out_s[str(rule["name"])] = cached
            if self._first_matching_landmark_group(out_s, groups):
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

    async def _verify_screen(
        self,
        image: np.ndarray,
        screen: ScreenName,
        *,
        frame_gray: np.ndarray | None = None,
    ) -> bool:
        """Sticky check: does this frame still satisfy ``screen``'s own rules?

        Runs ONLY the template landmark rules attached to ``screen`` in the
        per-module ``routes/screen_verify.yaml`` via in-process ``cv2.matchTemplate``.
        Short-circuits at the first matching landmark group.

        Big win on the steady-state case where the bot dwells on one screen
        for many ticks: skips the full multi-screen template scan.
        """
        name_s = str(screen)
        overlay_rules, overlay_rule_groups = self._landmark_overlay_rules_cached(
            name_s,
            name_prefix="screen_detector.verify",
        )
        if not overlay_rules:
            return False
        try:
            out = await _evaluate_overlay_rules_in_thread(
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
            return False
        return self._first_matching_landmark_group(out, overlay_rule_groups)

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
        return await self._detect_by_match_landmarks(
            image,
            try_first=full_try_first,
            frame_gray=frame_gray,
        )


def _dedup_key(rule: dict[str, Any]) -> tuple[str, str, float]:
    """Identity for a landmark rule, sharable across screens.

    Two rules with the same ``(action, region, threshold)`` produce the same
    ``cv2.matchTemplate`` verdict on the same frame, so we evaluate them once
    per ``detect_screen`` call and reuse the row.
    """
    action = normalize_overlay_action(rule) or "findIcon"
    region = str(rule.get("region") or "").strip()
    try:
        threshold = float(rule.get("threshold", 0.9))
    except (TypeError, ValueError):
        threshold = 0.9
    return (action, region, threshold)


_LANDMARK_PARALLEL_THRESHOLD = 6
"""Below this rule count, the chunk overhead outweighs cv2 parallelism gains."""


def _landmark_worker_count() -> int:
    """Worker count for parallel landmark batches.

    cv2.matchTemplate releases the GIL, so real CPU parallelism is possible.
    Cap at 4 to leave headroom for the worker's snapshot capture, OCR client,
    and Redis I/O on the same machine.
    """
    cpu = os.cpu_count() or 1
    return max(1, min(4, cpu))


async def _evaluate_overlay_rules_in_thread(
    image: np.ndarray,
    area_doc: dict[str, Any],
    root: Any,
    rules: list[dict[str, Any]],
    *,
    frame_gray: np.ndarray | None = None,
) -> dict[str, Any]:
    """Run the overlay engine off the event loop, optionally in parallel.

    Template matching (``cv2.matchTemplate``) is CPU-bound and otherwise blocks
    the worker's asyncio loop for hundreds of ms per detect — stalling the
    snapshot capture, OCR client, and Redis I/O behind it.

    Cold-path optimisation: when the batch has enough landmark rules to amortise
    the chunking overhead, split into ``_landmark_worker_count()`` shards and
    evaluate them in parallel threads via ``asyncio.gather``. Each shard runs
    its own ``asyncio.run`` over the engine on a subset of rules, which is safe
    because the engine's findIcon/tab_active paths don't share mutable state
    across rules — only read-only caches (template/region lookups).
    """

    def _run(subset: list[dict[str, Any]]) -> dict[str, Any]:
        return asyncio.run(
            evaluate_overlay_rules_async(
                image,
                area_doc,
                root,
                subset,
                frame_gray=frame_gray,
            )
        )

    n_workers = _landmark_worker_count()
    if n_workers <= 1 or len(rules) < _LANDMARK_PARALLEL_THRESHOLD:
        return await asyncio.to_thread(_run, rules)

    # Stripe rules across shards so each shard sees a mix of fast/slow rules.
    # Sequential chunking would put adjacent (likely-similar-cost) rules in the
    # same shard, leading to long-tail stragglers.
    shards: list[list[dict[str, Any]]] = [[] for _ in range(n_workers)]
    for idx, rule in enumerate(rules):
        shards[idx % n_workers].append(rule)
    shards = [shard for shard in shards if shard]

    shard_outs = await asyncio.gather(
        *(asyncio.to_thread(_run, shard) for shard in shards)
    )
    merged: dict[str, Any] = {}
    for shard_out in shard_outs:
        merged.update(shard_out)
    return merged


_suggest_detector_lock = threading.Lock()
_suggest_detector: ScreenDetector | None = None
_suggest_detector_area_mtime: float = 0.0


def _shared_suggest_detector() -> ScreenDetector:
    """Process-wide :class:`ScreenDetector` for the UI suggest helper.

    Building one means constructing ``OcrClient(load_settings())`` and paying
    the first-call cache warm-up. The labeling UI and overlay-test probe both
    hit this on every poll, so we cache the instance and invalidate its
    ``_area_doc`` only when an ``area.json`` / module area manifest mtime
    advances — that keeps labeling edits live without rebuilding the detector.
    """
    global _suggest_detector, _suggest_detector_area_mtime
    from config.loader import load_settings

    mtime = area_manifest_max_mtime(repo_root())
    with _suggest_detector_lock:
        if _suggest_detector is None:
            _suggest_detector = ScreenDetector(OcrClient(load_settings()))
            _suggest_detector_area_mtime = mtime
        elif mtime > _suggest_detector_area_mtime:
            _suggest_detector._area_doc = None
            _suggest_detector_area_mtime = mtime
        return _suggest_detector


def suggest_node_for_image_sync(
    image_bgr: np.ndarray,
    *,
    hint: ScreenName | str | None = None,
) -> str | None:
    """Best-effort node id for ``image_bgr`` — UI-friendly sync wrapper.

    Returns the screen id string (e.g. ``"main_city"``) on success, or ``None``
    when no template landmark fires.

    ``hint``: forwarded to :meth:`ScreenDetector.detect_screen` so callers that
    remember a likely screen (e.g. the worker's last known screen for this
    instance) can take the sticky verify fast path and skip the full multi-screen
    scan when the hint still holds.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return None

    detector = _shared_suggest_detector()
    try:
        result = asyncio.run(detector.detect_screen(image_bgr, hint=hint))
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
