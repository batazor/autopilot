from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from analysis.overlay_engine import evaluate_overlay_rules_async
from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc

# Side-effect imports: register dynamic-edge resolvers with screen_graph
# so edges in edge_taps.yaml can resolve at runtime.
from navigation import (
    calendar_go_resolver,  # noqa: F401
    hero_grid_resolver,  # noqa: F401
    main_menu_panel_resolver,  # noqa: F401
    tab_identify_resolver,  # noqa: F401
    tab_index_resolver,  # noqa: F401
    template_icon_resolver,  # noqa: F401
)
from navigation.detector import ScreenDetector, ScreenName
from navigation.nav_state import (
    SCREEN_HISTORY_MAX as _SCREEN_HISTORY_MAX,  # noqa: F401  (back-compat re-export)
)
from navigation.nav_state import NavStateStore
from navigation.screen_graph import (
    Tap,
    format_route_explain,
    route_hops_async,
    same_screen_family,
    screen_family_for,
    screen_verify_retry,
    screen_verify_rules,
)
from navigation.screen_verifier import ScreenVerifier
from navigation.tap_executor import (
    _NAV_TAP_SETTLE_S,
    TapExecutor,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from config.loader import Settings
    from ocr.client import OcrClient

logger = logging.getLogger(__name__)

_MAIN_CITY = ScreenName.MAIN_CITY

# _execute_hops outcomes: tap failed (rejected / blocked) vs screen never verified.
_HopExec = Literal["ok", "tap_failed", "verify_failed"]

# UI settle pauses after navigation taps. ``_NAV_TAP_SETTLE_S`` is imported from
# tap_executor (shared with the tap cluster); these are routing-only.
_NAV_HOP_SETTLE_S = 0.8
_NAV_UNKNOWN_RETRY_SETTLE_S = 0.8


class Navigator:
    def __init__(
        self,
        capture_fn: Callable[[str], np.ndarray],
        tap_fn: Callable[..., bool | None],
        *,
        system_back_fn: Callable[[str], bool | None] | None = None,
        swipe_fn: Callable[..., bool | None] | None = None,
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
        self._system_back = system_back_fn
        # Optional horizontal swipe (``(instance_id, start: Point, end: Point)``)
        # used by tab_identify to advance swipe-only tab strips (events panel).
        self._swipe = swipe_fn
        self._detector = ScreenDetector(ocr_client)
        self._ocr = ocr_client
        self._settings = settings
        self._redis = redis_client
        self._screen_state = NavStateStore(redis_client)
        self._area_doc: dict[str, Any] | None = None
        self._repo_root = repo_root()
        self._verifier = ScreenVerifier(
            # Late-bound through ``self`` (not the bound method captured now) so
            # an instance-level ``patch.object(nav, "_load_area_doc")`` in tests
            # still reaches the verifier.
            load_area_doc=lambda: self._load_area_doc(),
            get_ocr=lambda: self._ocr,
            repo_root=self._repo_root,
            screen_state=self._screen_state,
        )
        self._tap_executor = TapExecutor(self)

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

    # Tap execution (region/template/tab/calendar/panel + system-back + approval
    # signature introspection) lives in ``self._tap_executor`` (TapExecutor).
    # These forwarders keep the names the routing loop and tests call.
    async def _tap_region_name_async(
        self,
        instance_id: str,
        region_name: str,
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        return await self._tap_executor._tap_region_name_async(
            instance_id,
            region_name,
            from_screen=from_screen,
            to_screen=to_screen,
            state_flat=state_flat,
            path_csv=path_csv,
            hop_index=hop_index,
        )

    async def _tap_template_icon_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        return await self._tap_executor._tap_template_icon_async(
            instance_id,
            spec,
            from_screen=from_screen,
            to_screen=to_screen,
            state_flat=state_flat,
            path_csv=path_csv,
            hop_index=hop_index,
        )

    async def _tap_tab_index_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        return await self._tap_executor._tap_tab_index_async(
            instance_id,
            spec,
            from_screen=from_screen,
            to_screen=to_screen,
            state_flat=state_flat,
            path_csv=path_csv,
            hop_index=hop_index,
        )

    async def _tap_tab_identify_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        return await self._tap_executor._tap_tab_identify_async(
            instance_id,
            spec,
            from_screen=from_screen,
            to_screen=to_screen,
            state_flat=state_flat,
            path_csv=path_csv,
            hop_index=hop_index,
        )

    async def _tap_calendar_go_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        return await self._tap_executor._tap_calendar_go_async(
            instance_id,
            spec,
            from_screen=from_screen,
            to_screen=to_screen,
            state_flat=state_flat,
            path_csv=path_csv,
            hop_index=hop_index,
        )

    async def _tap_main_menu_panel_row_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        return await self._tap_executor._tap_main_menu_panel_row_async(
            instance_id,
            spec,
            from_screen=from_screen,
            to_screen=to_screen,
            state_flat=state_flat,
            path_csv=path_csv,
            hop_index=hop_index,
        )

    async def _system_back_async(self, instance_id: str) -> bool:
        return await self._tap_executor._system_back_async(instance_id)

    # Screen-state Redis IO is owned by ``self._screen_state`` (NavStateStore);
    # these methods forward to it so the ~40 internal call sites keep their
    # existing names while the key schema / transport details live in one place.
    def _state_key(self, instance_id: str) -> str:
        return self._screen_state.state_key(instance_id)

    def _history_key(self, instance_id: str) -> str:
        return self._screen_state.history_key(instance_id)

    async def _set_nav_expected_screen(self, instance_id: str, screen: str) -> None:
        await self._screen_state.set_expected_screen(instance_id, screen)

    async def _clear_nav_expected_screen(self, instance_id: str) -> None:
        await self._screen_state.clear_expected_screen(instance_id)

    async def _write_screen(self, instance_id: str, screen: str) -> None:
        await self._screen_state.write_screen(instance_id, screen)

    async def _write_nav_error(self, instance_id: str, detail: str) -> None:
        await self._screen_state.write_error(instance_id, detail)

    async def _clear_nav_error(self, instance_id: str) -> None:
        await self._screen_state.clear_error(instance_id)

    async def _region_visible_async(
        self,
        image_bgr: np.ndarray,
        region_name: str,
        *,
        state_flat: dict[str, Any] | None,
    ) -> bool:
        area_doc = self._load_area_doc()
        pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
        if pair is None:
            return False
        _entry, reg = pair
        try:
            threshold = float(reg.get("threshold", 0.8))
        except (TypeError, ValueError):
            threshold = 0.8
        rule = {
            "name": "navigator.region_visible",
            "region": region_name,
            "action": "findIcon",
            "threshold": threshold,
        }
        try:
            out = await evaluate_overlay_rules_async(
                image_bgr,
                area_doc,
                self._repo_root,
                [rule],
                state_flat=state_flat,
            )
        except Exception:
            logger.debug(
                "Navigator: region visibility probe failed for %s",
                region_name,
                exc_info=True,
            )
            return False
        row = out.get("navigator.region_visible")
        return bool(isinstance(row, dict) and row.get("matched"))

    async def _try_family_tab_advance(
        self,
        instance_id: str,
        *,
        current: str,
        target: str,
        image_bgr: np.ndarray,
        state_flat: dict[str, Any] | None,
    ) -> bool:
        if not same_screen_family(current, target):
            return False
        family = screen_family_for(current)
        if family is None:
            return False
        family_name, cfg = family
        next_region = str(cfg.get("next_region") or "").strip()
        if not next_region:
            return False
        if not await self._region_visible_async(
            image_bgr,
            next_region,
            state_flat=state_flat,
        ):
            logger.info(
                "Navigator: same-family route %s -> %s has no visible advance region %s",
                current,
                target,
                next_region,
            )
            return False
        logger.info(
            "Navigator: trying local %s tab advance %s before main_city fallback "
            "(%s -> %s)",
            family_name,
            next_region,
            current,
            target,
        )
        return await self._tap_region_name_async(
            instance_id,
            next_region,
            state_flat=state_flat,
            from_screen=current,
            to_screen=target,
        )

    async def _screen_history(self, instance_id: str) -> list[str]:
        return await self._screen_state.screen_history(instance_id)

    # Image-based verify rules live in ``self._verifier`` (ScreenVerifier);
    # these forwarders preserve the names that internal callers use and that
    # tests patch / call directly (e.g. ``_verify_rule``,
    # ``_verify_from_screen_rule``). Internal callers must keep using
    # ``self._verify_rule`` so a ``mocker.patch.object(nav, "_verify_rule")``
    # still intercepts.
    async def _verify_match_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        return await self._verifier.verify_match_rule(image, rule, state_flat=state_flat)

    async def _verify_ocr_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        return await self._verifier.verify_ocr_rule(image, rule, state_flat=state_flat)

    async def _verify_tab_active_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        return await self._verifier.verify_tab_active_rule(
            image, rule, state_flat=state_flat
        )

    async def _verify_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
        instance_id: str | None = None,
    ) -> bool:
        return await self._verifier.verify_rule(
            image, rule, state_flat=state_flat, instance_id=instance_id
        )

    async def _verify_from_screen_rule(
        self,
        rule: dict[str, Any],
        *,
        instance_id: str | None,
    ) -> bool:
        return await self._verifier.verify_from_screen_rule(
            rule, instance_id=instance_id
        )

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
            detected = await self._detector.detect_screen(image, expected=candidate)
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
            detected = await self._detector.detect_screen(image, expected=target)
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

    async def _screen_verified_once(
        self,
        instance_id: str,
        target: str,
        image: np.ndarray,
        *,
        state_flat: dict[str, Any] | None,
    ) -> bool:
        detected = await self._detector.detect_screen(image, expected=target)
        if str(detected) == target:
            return True
        for rule in screen_verify_rules(target):
            if "from_screen" in rule:
                continue
            if await self._verify_rule(
                image,
                rule,
                state_flat=state_flat,
                instance_id=instance_id,
            ):
                return True
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
        await self._set_nav_expected_screen(instance_id, str(target))
        try:
            return await self._navigate_to_impl(target, instance_id)
        finally:
            await self._clear_nav_expected_screen(instance_id)

    async def _navigate_to_impl(self, target: ScreenName, instance_id: str) -> bool:
        # Track consecutive ``UNKNOWN`` ticks where neither the screen
        # detector nor the back-button heuristic finds anything actionable.
        # That state means something opaque (typically a full-screen ad
        # popup) is overlaying the UI: the navigator alone can't dismiss it,
        # and looping for the full 10 attempts blocks the worker for ~8s
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
            current = await self._detector.detect_screen(image, expected=target)

            if current == target:
                await self._write_screen(instance_id, str(target))
                await self._clear_nav_error(instance_id)
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
                if await self._ui_page_back_visible(img):
                    consec_unknown_no_back = 0
                    if not await self._tap_region_name_async(
                        instance_id,
                        "icon.page.back",
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
                await asyncio.sleep(_NAV_UNKNOWN_RETRY_SETTLE_S)
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

            if (
                hop_sequences
                and current != _MAIN_CITY
                and str(hop_sequences[0][0]) == str(_MAIN_CITY)
                and await self._try_family_tab_advance(
                    instance_id,
                    current=str(current),
                    target=str(target),
                    image_bgr=image,
                    state_flat=state_flat,
                )
            ):
                await asyncio.sleep(_NAV_HOP_SETTLE_S)
                continue

            if hop_sequences is None and current != _MAIN_CITY:
                if await self._try_family_tab_advance(
                    instance_id,
                    current=str(current),
                    target=str(target),
                    image_bgr=image,
                    state_flat=state_flat,
                ):
                    await asyncio.sleep(_NAV_HOP_SETTLE_S)
                    continue
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
                    await self._write_nav_error(
                        instance_id,
                        "navigation route failed before main_city fallback\n"
                        + format_route_explain(str(current), str(target)),
                    )
                    img2: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                    if await self._ui_page_back_visible(img2):
                        if not await self._tap_region_name_async(
                            instance_id,
                            "icon.page.back",
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
                    await asyncio.sleep(_NAV_UNKNOWN_RETRY_SETTLE_S)
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
                        await self._clear_nav_error(instance_id)
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
                    await self._write_nav_error(
                        instance_id,
                        "navigation path unavailable\n"
                        + format_route_explain(str(current), str(target)),
                    )
                    return False
                continue

            hr = await self._execute_hops(
                instance_id, hop_sequences, from_screen=str(current)
            )
            if hr == "ok":
                await self._clear_nav_error(instance_id)
                return True
            if hr == "tap_failed":
                # Tap rejected; the previous ``_write_screen``
                # (intermediate identity) still reflects reality.
                return False

        logger.error("Failed to navigate to %s after 10 attempts", target)
        await self._write_nav_error(
            instance_id,
            "navigation failed after retries\n"
            + format_route_explain(str(current), str(target)),
        )
        await self._write_screen(instance_id, "")
        return False

    async def _execute_hops(
        self,
        instance_id: str,
        hop_sequences: list[tuple[str, list[Tap]]],
        *,
        from_screen: str | None = None,
    ) -> _HopExec:
        src_screen = str(from_screen or "")
        state_flat = await self._active_player_state_flat(instance_id)
        # Pre-compute the full path so each per-hop approval carries the same
        # full route and an index of the *destination* of the current hop.
        # Approvals UI uses this to render the route with the current
        # transition highlighted (operator sees ``main_city → bold(exploration)
        # → squad_settings`` rather than just the local edge).
        full_path: list[str] = [src_screen] + [str(dst) for dst, _ in hop_sequences]
        path_csv = ",".join(s for s in full_path if s)
        route_target = str(hop_sequences[-1][0]) if hop_sequences else ""
        for hop_idx, (dst_screen, taps) in enumerate(hop_sequences, start=1):
            await self._set_nav_expected_screen(instance_id, str(dst_screen))
            for point in taps:
                # Static taps are region names; dynamic resolvers may return
                # structured specs that resolve against the current frame.
                if isinstance(point, dict) and point.get("type") == "system_back":
                    tapped = await self._system_back_async(instance_id)
                elif isinstance(point, dict) and point.get("type") == "template_icon":
                    tapped = await self._tap_template_icon_async(
                        instance_id,
                        point,
                        from_screen=src_screen,
                        to_screen=str(dst_screen),
                        state_flat=state_flat,
                        path_csv=path_csv,
                        hop_index=hop_idx,
                    )
                elif isinstance(point, dict) and point.get("type") == "tab_index":
                    tapped = await self._tap_tab_index_async(
                        instance_id,
                        point,
                        from_screen=src_screen,
                        to_screen=str(dst_screen),
                        state_flat=state_flat,
                        path_csv=path_csv,
                        hop_index=hop_idx,
                    )
                elif isinstance(point, dict) and point.get("type") == "tab_identify":
                    tapped = await self._tap_tab_identify_async(
                        instance_id,
                        point,
                        from_screen=src_screen,
                        to_screen=str(dst_screen),
                        state_flat=state_flat,
                        path_csv=path_csv,
                        hop_index=hop_idx,
                    )
                elif isinstance(point, dict) and point.get("type") == "calendar_go":
                    tapped = await self._tap_calendar_go_async(
                        instance_id,
                        point,
                        from_screen=src_screen,
                        to_screen=str(dst_screen),
                        state_flat=state_flat,
                        path_csv=path_csv,
                        hop_index=hop_idx,
                    )
                elif (
                    isinstance(point, dict)
                    and point.get("type") == "main_menu_panel_row"
                ):
                    tapped = await self._tap_main_menu_panel_row_async(
                        instance_id,
                        point,
                        from_screen=src_screen,
                        to_screen=str(dst_screen),
                        state_flat=state_flat,
                        path_csv=path_csv,
                        hop_index=hop_idx,
                    )
                else:
                    tapped = await self._tap_region_name_async(
                        instance_id,
                        str(point),
                        from_screen=src_screen,
                        to_screen=str(dst_screen),
                        state_flat=state_flat,
                        path_csv=path_csv,
                        hop_index=hop_idx,
                    )
                if not tapped:
                    logger.info(
                        "Navigator: navigation tap not executed (rejected, blocked, or bad region) "
                        "on %s — aborting route",
                        instance_id,
                    )
                    await self._clear_nav_expected_screen(instance_id)
                    return "tap_failed"
                await asyncio.sleep(_NAV_TAP_SETTLE_S)
            await asyncio.sleep(_NAV_HOP_SETTLE_S)
            # Some routes intentionally go through a generic parent node before
            # a tab-specific node. If the first tap opens the final tab directly
            # (e.g. main_city -> survivor_status.status), don't fail the parent
            # hop just because the detector returned the more specific screen.
            if (
                route_target
                and route_target != str(dst_screen)
                and route_target.startswith(f"{dst_screen}.")
            ):
                image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
                if await self._screen_verified_once(
                    instance_id,
                    route_target,
                    image,
                    state_flat=state_flat,
                ):
                    await self._write_screen(instance_id, route_target)
                    return "ok"
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
                await self._clear_nav_expected_screen(instance_id)
                return "verify_failed"
        await self._clear_nav_expected_screen(instance_id)
        return "ok"
