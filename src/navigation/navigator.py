from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any, Literal

import numpy as np

from analysis.overlay_engine import evaluate_overlay_rules_async
from config.loader import Settings
from config.paths import repo_root
from layout.area_manifest import load_area_doc
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_random_point_to_device_point
from layout.types import Region

# Side-effect imports: register dynamic-edge resolvers with screen_graph
# so edges in edge_taps.yaml can resolve at runtime.
from navigation import (
    event_blocks_resolver,  # noqa: F401
    hero_grid_resolver,  # noqa: F401
)
from navigation.detector import ScreenDetector, ScreenName
from navigation.screen_graph import (
    route_hops_async,
    screen_verify_retry,
    screen_verify_rules,
)
from ocr.client import OcrClient
from ocr.fuzzy import match as fuzzy_match

logger = logging.getLogger(__name__)

_MAIN_CITY = ScreenName.MAIN_CITY

# _execute_hops outcomes: tap failed (rejected / blocked) vs screen never verified.
_HopExec = Literal["ok", "tap_failed", "verify_failed"]

_SCREEN_HISTORY_MAX = 5
"""Cap on the rolling history kept at ``wos:instance:<id>:screen_history``.
Long enough to recover hop context (e.g. ``page.heroes.ahmose`` → wiki) after a
few intermediate transitions; short enough to keep LTRIM cheap. Index 0 is the
most recent entry."""


class Navigator:
    def __init__(
        self,
        capture_fn: Callable[[str], np.ndarray],
        tap_fn: Callable[..., bool | None],
        *,
        settings: Settings,
        ocr_client: OcrClient,
        redis_client: Any | None = None,
    ) -> None:
        # ``tap_fn`` is loosely typed (``Callable[..., bool | None]``) because
        # callers pass either the legacy 2-arg shape (``(instance_id, point)``)
        # or the modern 4-kwarg form (``approval_region``, ``approval_source``,
        # ``approval_context``); ``_tap_supports_approval_source`` introspects
        # via ``inspect.signature`` to pick the right calling shape.
        self._capture = capture_fn
        self._tap = tap_fn
        self._detector = ScreenDetector(ocr_client)
        self._ocr = ocr_client
        self._settings = settings
        self._redis = redis_client
        self._area_doc: dict[str, Any] | None = None
        self._repo_root = repo_root()
        self._tap_accepts_approval_source: bool | None = None

    def _load_area_doc(self) -> dict[str, Any]:
        if self._area_doc is not None:
            return self._area_doc

        self._area_doc = load_area_doc(self._repo_root)
        return self._area_doc

    async def _active_player_state_flat(
        self, instance_id: str
    ) -> dict[str, Any] | None:
        """Per-instance flat state dict so version-aware region lookups resolve overrides.

        Without this, ``screen_region_by_name`` only sees the base ``regions[]`` list,
        and any version-only region (e.g. ``main_city.to.exploration`` that lives
        only in ``versions[v2].regions[]``) would be reported as unknown.
        """
        if self._redis is None:
            return None
        try:
            row = await self._redis.hgetall(self._state_key(instance_id))
        except Exception:
            return None
        if not row:
            return None
        decoded = {
            (k.decode() if isinstance(k, bytes) else str(k)):
                (v.decode() if isinstance(v, bytes) else str(v))
            for k, v in row.items()
        }
        active = decoded.get("active_player", "").strip()
        if not active:
            return None
        try:
            from config.state_store import get_state_store

            return get_state_store().get_or_create(active).to_flat_dict()
        except Exception:
            logger.debug(
                "Navigator: state_flat lookup failed for player=%s",
                active,
                exc_info=True,
            )
            return None

    def _tap_region_name(
        self,
        instance_id: str,
        region_name: str,
        *,
        dev_w: int,
        dev_h: int,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        area_doc = self._load_area_doc()
        tap_variant = f"{region_name}_tap"
        pair = screen_region_by_name(
            area_doc, tap_variant, state_flat=state_flat
        ) or screen_region_by_name(area_doc, region_name, state_flat=state_flat)
        if pair is None:
            logger.warning("Navigator: unknown region %r in area.json", region_name)
            return False
        _entry, reg = pair
        bbox = reg.get("bbox")
        if not isinstance(bbox, dict):
            logger.warning("Navigator: region %r missing bbox", region_name)
            return False
        pt = bbox_percent_random_point_to_device_point(bbox, dev_w, dev_h)
        approval_context: dict[str, Any] = {}
        if from_screen:
            approval_context["from_screen"] = from_screen
        if to_screen:
            approval_context["to_screen"] = to_screen
        # Full path (CSV of screen ids) + 1-based index of the destination
        # being tapped right now. Both are optional — single-hop / non-route
        # taps via ``_tap_region_name`` skip them and the approvals UI falls
        # back to rendering just the local edge as before.
        if path_csv:
            approval_context["path"] = path_csv
        if hop_index is not None:
            approval_context["hop_index"] = str(hop_index)
        if self._tap_supports_approval_source():
            return bool(
                self._tap(
                    instance_id,
                    pt,
                    approval_region=str(reg.get("name") or region_name),
                    approval_source="navigation",
                    approval_context=approval_context,
                )
            )  # type: ignore[operator]
        return bool(
            self._tap(
                instance_id,
                pt,
                approval_region=str(reg.get("name") or region_name),
            )
        )  # type: ignore[operator]

    async def _tap_region_name_async(
        self,
        instance_id: str,
        region_name: str,
        *,
        dev_w: int,
        dev_h: int,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        """Run ADB tap/approval wait off the event loop so rolling preview keeps ticking."""
        return bool(
            await asyncio.to_thread(
                self._tap_region_name,
                instance_id,
                region_name,
                dev_w=dev_w,
                dev_h=dev_h,
                from_screen=from_screen,
                to_screen=to_screen,
                state_flat=state_flat,
                path_csv=path_csv,
                hop_index=hop_index,
            )
        )

    def _tap_supports_approval_source(self) -> bool:
        if self._tap_accepts_approval_source is not None:
            return self._tap_accepts_approval_source
        try:
            sig = inspect.signature(self._tap)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self._tap_accepts_approval_source = False
            return False
        self._tap_accepts_approval_source = (
            "approval_source" in sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        )
        return self._tap_accepts_approval_source

    def _state_key(self, instance_id: str) -> str:
        return f"wos:instance:{instance_id}:state"

    def _history_key(self, instance_id: str) -> str:
        return f"wos:instance:{instance_id}:screen_history"

    async def _write_screen(self, instance_id: str, screen: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset(self._state_key(instance_id), "current_screen", screen)
        except Exception:
            logger.debug("Navigator: failed to write current_screen to Redis", exc_info=True)
        # Push to rolling history. Skip empty strings (those represent "unknown"
        # after a verify failure — a history entry there would suggest the bot
        # was on a real "unknown" screen, which confuses ``from_screen`` rules
        # that look one hop back). De-dupe consecutive duplicates so navigating
        # back to a screen we were already on doesn't push a useless repeat.
        screen_s = str(screen or "").strip()
        if not screen_s:
            return
        try:
            head = await self._redis.lindex(self._history_key(instance_id), 0)
            head_s = (head.decode() if isinstance(head, bytes) else str(head or "")).strip()
            if head_s == screen_s:
                return
            await self._redis.lpush(self._history_key(instance_id), screen_s)
            await self._redis.ltrim(
                self._history_key(instance_id), 0, _SCREEN_HISTORY_MAX - 1
            )
        except Exception:
            logger.debug(
                "Navigator: failed to push screen history to Redis", exc_info=True
            )

    async def _screen_history(self, instance_id: str) -> list[str]:
        """Most-recent-first list of screens previously written by this navigator.

        Index 0 is the current screen; index 1 the one before, and so on. Empty
        list when Redis is absent or the key was never populated.
        """
        if self._redis is None:
            return []
        try:
            raw = await self._redis.lrange(
                self._history_key(instance_id), 0, _SCREEN_HISTORY_MAX - 1
            )
        except Exception:
            logger.debug(
                "Navigator: failed to read screen history from Redis", exc_info=True
            )
            return []
        out: list[str] = []
        for item in raw or []:
            s = (item.decode() if isinstance(item, bytes) else str(item or "")).strip()
            if s:
                out.append(s)
        return out

    async def _verify_match_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
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
                state_flat=state_flat,
            )
        except Exception:
            logger.debug("Navigator: match verify failed for %s", region, exc_info=True)
            return False
        row = out.get(str(overlay_rule["name"]))
        return bool(isinstance(row, dict) and row.get("matched"))

    async def _verify_ocr_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        region = str(rule.get("ocr") or "").strip()
        if not region:
            return False
        pair = screen_region_by_name(self._load_area_doc(), region, state_flat=state_flat)
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
            result = await self._ocr.ocr_region(image, Region(px, py, pw, ph), region_id=region)
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

    async def _verify_tab_active_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        region = str(rule.get("tab_active") or "").strip()
        if not region:
            return False
        overlay_rule: dict[str, Any] = {
            "name": f"navigator.verify.{region}.active",
            "region": region,
            "isTabActive": True,
        }
        try:
            out = await evaluate_overlay_rules_async(
                image,
                self._load_area_doc(),
                self._repo_root,
                [overlay_rule],
                state_flat=state_flat,
            )
        except Exception:
            logger.debug("Navigator: tab-active verify failed for %s", region, exc_info=True)
            return False
        row = out.get(str(overlay_rule["name"]))
        return bool(isinstance(row, dict) and row.get("matched"))

    async def _verify_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
        instance_id: str | None = None,
    ) -> bool:
        if "from_screen" in rule:
            return await self._verify_from_screen_rule(
                rule, instance_id=instance_id
            )
        if "match" in rule:
            return await self._verify_match_rule(image, rule, state_flat=state_flat)
        if "ocr" in rule:
            return await self._verify_ocr_rule(image, rule, state_flat=state_flat)
        if "tab_active" in rule:
            return await self._verify_tab_active_rule(image, rule, state_flat=state_flat)
        return False

    async def _verify_from_screen_rule(
        self,
        rule: dict[str, Any],
        *,
        instance_id: str | None,
    ) -> bool:
        """True when the most-recent screen_history entry matches ``from_screen``.

        ``_wait_for_screen_verified`` runs BEFORE ``_write_screen(target)``, so
        at evaluation time index 0 of the history is the source screen of the
        hop we just took. Only that immediate predecessor is checked: a wider
        window would let an unrelated intermediate hop "validate" a wiki popup
        that we never actually opened.
        """
        if not instance_id:
            return False
        accepted_raw = rule.get("from_screen")
        accepted: list[str] = []
        if isinstance(accepted_raw, list):
            accepted = [str(x).strip() for x in accepted_raw if str(x).strip()]
        elif accepted_raw is not None and str(accepted_raw).strip():
            accepted = [str(accepted_raw).strip()]
        if not accepted:
            return False
        history = await self._screen_history(instance_id)
        prev = history[0] if history else ""
        return prev in accepted

    async def _ui_page_back_visible(self, image: np.ndarray) -> bool:
        """True when ``icon.page.back`` template matches in ``area.json`` (safe to tap)."""
        return await self._verify_match_rule(
            image,
            {"match": "icon.page.back", "threshold": 0.9},
        )

    async def recover_screen_from_history(self, instance_id: str) -> str:
        """Re-confirm the last known screen when ``current_screen`` is empty.

        Closes a race that bites scenarios with a ``node:`` clause: the worker
        pops the task, ``current_screen`` is empty (a prior verify failure or
        an in-flight transition cleared it), and the scenario early-exits with
        ``awaiting_screen_identity`` — even though the device is still sitting
        on the screen we were on a heartbeat ago. The screen_history rolling
        list remembers the most recent real screen; if its image-based verify
        rules pass right now, we trust the previous identity and re-publish
        ``current_screen`` so the scenario can proceed.

        Returns the recovered screen name on success, ``""`` when no usable
        history exists or its rules don't match the live frame. ``from_screen``
        rules are skipped — they read the same history list and would yield a
        circular pass.
        """
        history = await self._screen_history(instance_id)
        if not history:
            return ""
        candidate = history[0]
        rules = screen_verify_rules(candidate)
        image_rules = [r for r in rules if "from_screen" not in r]
        if not image_rules:
            return ""
        try:
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
        except Exception:
            logger.debug(
                "Navigator: history recovery capture failed instance=%s",
                instance_id,
                exc_info=True,
            )
            return ""
        # Cheap path first: the screen detector already covers every screen
        # listed in ``screen_verify.yaml`` via landmarks, so a positive
        # detection here makes the per-rule loop redundant.
        try:
            detected = await self._detector.detect_screen(image)
        except Exception:
            detected = ScreenName.UNKNOWN
        if str(detected) == candidate:
            await self._write_screen(instance_id, candidate)
            return candidate
        state_flat = await self._active_player_state_flat(instance_id)
        for rule in image_rules:
            if await self._verify_rule(
                image, rule, state_flat=state_flat, instance_id=instance_id
            ):
                await self._write_screen(instance_id, candidate)
                return candidate
        return ""

    async def _wait_for_screen_verified(self, instance_id: str, target: str) -> bool:
        attempts, interval_seconds = screen_verify_retry(target)
        rules = screen_verify_rules(target)
        state_flat = await self._active_player_state_flat(instance_id)
        # ``from_screen`` rules don't depend on the framebuffer — short-circuit
        # before the capture loop so we don't burn the (attempts × interval)
        # budget on rules that can decide from Redis history alone.
        for rule in rules:
            if "from_screen" in rule and await self._verify_rule(
                np.empty((0, 0, 3), dtype=np.uint8),
                rule,
                state_flat=state_flat,
                instance_id=instance_id,
            ):
                return True
        for attempt in range(1, attempts + 1):
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
            detected = await self._detector.detect_screen(image)
            if str(detected) == target:
                return True
            for rule in rules:
                if "from_screen" in rule:
                    continue
                if await self._verify_rule(
                    image, rule, state_flat=state_flat, instance_id=instance_id
                ):
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
        # Trusts ScreenDetector: it already covers match landmarks. Every
        # routable screen in screen_verify.yaml should expose template
        # landmarks, so the historic post-detector fan-out over
        # `screen_verify_screen_names() × rules` was pure duplication.
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
        # Track consecutive ``UNKNOWN`` ticks where neither the screen
        # detector nor the back-button heuristic finds anything actionable.
        # That state means something opaque (typically a full-screen ad
        # popup) is overlaying the UI: the navigator alone can't dismiss it,
        # and looping for the full 10 attempts blocks the worker for ~15s
        # while the overlay scanner — which would push ``tap_ads_*`` /
        # ``skip_button`` / etc. — is starved on the same worker thread.
        # Bailing after a couple of these stuck ticks lets the scenario
        # fail fast; the queue then runs the higher-priority popup
        # dismissal, and the natural re-push (identity probe, overlay,
        # cron) brings the scenario back when the screen is clear.
        _UNKNOWN_NO_BACK_LIMIT = 2
        consec_unknown_no_back = 0

        for attempt in range(10):
            state_flat = await self._active_player_state_flat(instance_id)
            image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
            current = await self._detector.detect_screen(image)

            if current == target:
                await self._write_screen(instance_id, str(target))
                return True

            if current == ScreenName.UNKNOWN:
                logger.warning(
                    "Navigator: screen not recognized (target=%s, instance=%s, "
                    "navigate_attempt=%d/10); will tap back if icon.page.back is visible",
                    target,
                    instance_id,
                    attempt + 1,
                )
                await self._write_screen(instance_id, "")
                img: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                dev_h, dev_w = int(img.shape[0]), int(img.shape[1])
                if await self._ui_page_back_visible(img):
                    consec_unknown_no_back = 0
                    if not await self._tap_region_name_async(
                        instance_id,
                        "icon.page.back",
                        dev_w=dev_w,
                        dev_h=dev_h,
                        state_flat=state_flat,
                    ):
                        # Tap was rejected (approval mode) or icon.page.back bbox is gone:
                        # either way, retrying for 10 attempts spams the approval UI and
                        # ignores the user's explicit "no". Bail so the caller (DSL
                        # scenario) can abort cleanly.
                        logger.info(
                            "Navigator: icon.page.back tap not executed on %s — aborting "
                            "navigation to %s",
                            instance_id, target,
                        )
                        return False
                else:
                    consec_unknown_no_back += 1
                    logger.warning(
                        "Navigator: screen not recognized (target=%s, instance=%s, "
                        "navigate_attempt=%d/10); icon.page.back not visible — "
                        "skipping back tap (consec=%d/%d)",
                        target,
                        instance_id,
                        attempt + 1,
                        consec_unknown_no_back,
                        _UNKNOWN_NO_BACK_LIMIT,
                    )
                    if consec_unknown_no_back >= _UNKNOWN_NO_BACK_LIMIT:
                        logger.info(
                            "Navigator: %d consecutive UNKNOWN-no-back ticks for %s "
                            "(target=%s) — bailing so overlay scanner can dismiss "
                            "the blocker (ad / popup / loading frame)",
                            consec_unknown_no_back, instance_id, target,
                        )
                        return False
                await asyncio.sleep(1.5)
                continue
            # Recognised some screen (just not the target) — reset the
            # stuck-UNKNOWN counter; the loop is making progress.
            consec_unknown_no_back = 0

            # Persist the live identity even on intermediate hops. Without
            # this, ``current_screen`` only gets a fresh write when the
            # target is reached or detection returns UNKNOWN — so a single
            # transient UNKNOWN tick blanks the field, and every subsequent
            # iteration that recognises the real screen silently leaves the
            # empty value in Redis. The approvals UI, overlay router, and
            # any consumer that reads ``current_screen`` would see "no
            # identity" while the device is plainly on a known page (e.g.
            # an approval-blocked tap on the heroes roster shows the
            # ``"from_screen": "heroes"`` payload but ``current_screen=""``).
            await self._write_screen(instance_id, str(current))

            # Try direct BFS route (src → dst).
            hop_sequences = await route_hops_async(
                str(current), str(target),
                instance_id=instance_id, redis_client=self._redis,
            )

            if hop_sequences is None and current != _MAIN_CITY:
                # No direct route or missing taps: go main_city first, then retry.
                to_hub = await route_hops_async(
                    str(current), str(_MAIN_CITY),
                    instance_id=instance_id, redis_client=self._redis,
                )
                if to_hub:
                    hr = await self._execute_hops(
                        instance_id, to_hub, from_screen=str(current)
                    )
                    if hr == "tap_failed":
                        # The tap was rejected (approval blocked, missing
                        # region, …) — the device didn't move, so the
                        # ``current`` identity we wrote a few lines above
                        # still holds. Don't wipe it here.
                        return False
                else:
                    logger.warning(
                        "No route %s → main_city on %s; considering icon.page.back",
                        current,
                        instance_id,
                    )
                    img2: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                    dev_h2, dev_w2 = int(img2.shape[0]), int(img2.shape[1])
                    if await self._ui_page_back_visible(img2):
                        if not await self._tap_region_name_async(
                            instance_id,
                            "icon.page.back",
                            dev_w=dev_w2,
                            dev_h=dev_h2,
                            state_flat=state_flat,
                        ):
                            logger.info(
                                "Navigator: icon.page.back tap not executed on %s — "
                                "aborting navigation to %s",
                                instance_id, target,
                            )
                            return False
                    else:
                        logger.warning(
                            "No route to main_city and icon.page.back not visible on %s; not tapping",
                            instance_id,
                        )
                    await asyncio.sleep(1.5)
                continue

            if hop_sequences is None:
                # Already at main_city but no path to target.
                from_hub = await route_hops_async(
                    str(_MAIN_CITY), str(target),
                    instance_id=instance_id, redis_client=self._redis,
                )
                if from_hub:
                    hr = await self._execute_hops(
                        instance_id, from_hub, from_screen=str(_MAIN_CITY)
                    )
                    if hr == "ok":
                        return True
                    if hr == "tap_failed":
                        # Tap rejected; the previous ``_write_screen``
                        # (intermediate identity) still reflects reality.
                        return False
                else:
                    logger.info(
                        "No navigation path from %s to %s (and no route via main_city)",
                        current,
                        target,
                    )
                    return False
                continue

            hr = await self._execute_hops(
                instance_id, hop_sequences, from_screen=str(current)
            )
            if hr == "ok":
                return True
            if hr == "tap_failed":
                # Tap rejected; the previous ``_write_screen``
                # (intermediate identity) still reflects reality.
                return False

        logger.error("Failed to navigate to %s after 10 attempts", target)
        await self._write_screen(instance_id, "")
        return False

    async def _execute_hops(
        self,
        instance_id: str,
        hop_sequences: list[tuple[str, list[str]]],
        *,
        from_screen: str | None = None,
    ) -> _HopExec:
        # Use current framebuffer size for percent->pixel mapping.
        img: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
        dev_h, dev_w = int(img.shape[0]), int(img.shape[1])
        src_screen = str(from_screen or "")
        state_flat = await self._active_player_state_flat(instance_id)
        # Pre-compute the full path so each per-hop approval carries the same
        # full route and an index of the *destination* of the current hop.
        # Approvals UI uses this to render the route with the current
        # transition highlighted (operator sees ``main_city → bold(exploration)
        # → squad_settings`` rather than just the local edge).
        full_path: list[str] = [src_screen] + [str(dst) for dst, _ in hop_sequences]
        path_csv = ",".join(s for s in full_path if s)
        for hop_idx, (dst_screen, taps) in enumerate(hop_sequences, start=1):
            for point in taps:
                # Tap steps are always region names (strings).
                if not await self._tap_region_name_async(
                    instance_id,
                    str(point),
                    dev_w=dev_w,
                    dev_h=dev_h,
                    from_screen=src_screen,
                    to_screen=str(dst_screen),
                    state_flat=state_flat,
                    path_csv=path_csv,
                    hop_index=hop_idx,
                ):
                    logger.info(
                        "Navigator: navigation tap not executed (rejected, blocked, or bad region) "
                        "on %s — aborting route",
                        instance_id,
                    )
                    return "tap_failed"
                await asyncio.sleep(0.8)
            await asyncio.sleep(1.5)
            if await self._wait_for_screen_verified(instance_id, str(dst_screen)):
                await self._write_screen(instance_id, str(dst_screen))
                src_screen = str(dst_screen)
            else:
                logger.warning(
                    "Navigator: destination %s was not verified on %s",
                    dst_screen,
                    instance_id,
                )
                await self._write_screen(instance_id, "")
                return "verify_failed"
        return "ok"
