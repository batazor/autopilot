from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from analysis.overlay import run_overlay_analysis
from analysis.overlay_ttl_state import (
    maybe_persist_overlay_ttl_state_to_redis,
    sync_overlay_ttl_state_if_needed,
)
from config.log_context import set_log_context
from config.paths import repo_root
from config.tracing import dismiss_unknown_popup_counter
from navigation.detector import ScreenName

logger = logging.getLogger(__name__)



if TYPE_CHECKING:
    import numpy as np

    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerScreenMixin(_Base):
    _cfg: Any
    _redis: Any
    _bot_actions: Any
    _queue: Any
    _screen_detector: Any
    _overlay_rule_eval_state_by_player: dict[str, dict[str, float]]
    _overlay_ttl_rev_by_player: dict[str, str]
    _overlay_ttl_last_sync_mono_by_player: dict[str, float]
    _overlay_ttl_last_persist_mono_by_player: dict[str, float]
    _last_current_screen: str | None
    _last_detected_screen: str | None
    _last_detected_screen_at: float
    _unknown_since: float
    _screen_unknown_streak: int

    async def _schedule_overlay_matches(
        self,
        overlay_results: dict[str, object],
        *,
        active_player: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        raise NotImplementedError

    def _grab_layout_bgr(self) -> np.ndarray:
        return self._bot_actions.capture_screen_bgr_direct(self._cfg.instance_id)

    def _grab_layout_bgr_cached(self, *, max_age_ms: float = 1000.0) -> np.ndarray:
        """Reuse the rolling-loop frame when fresh; falls back to direct capture.

        Saves the per-tick ``adb exec-out screencap`` fork when the rolling
        capture has already produced a frame within ``max_age_ms``.
        """
        capture = getattr(self._bot_actions, "capture_screen_bgr_cached", None)
        if capture is None:
            return self._grab_layout_bgr()
        return capture(self._cfg.instance_id, max_age_ms=max_age_ms)

    async def _overlay_analyze_bgr(
        self,
        image_bgr: np.ndarray,
        *,
        current_screen_override: str | None = None,
        device_level_only: bool = False,
    ) -> None:
        """Run ``analyze/analyze.yaml`` overlay rules on an ADB frame (BGR)."""
        root = repo_root()
        try:
            current_screen: str | None = current_screen_override
            active_player: str | None = None
            if self._redis is not None:
                state_key = f"wos:instance:{self._cfg.instance_id}:state"

                def _field(raw: object) -> str:
                    if raw is None:
                        return ""
                    return (
                        raw.decode() if isinstance(raw, bytes) else str(raw)
                    ).strip()

                cur_raw, ap_raw, tm_raw = await self._redis.hmget(
                    state_key, "current_screen", "active_player", "test_module"
                )
                if current_screen is None:
                    cur = _field(cur_raw)
                    current_screen = cur or None
                ap = _field(ap_raw)
                active_player = ap or None
                test_module = _field(tm_raw) or None
            else:
                test_module = None

            self._last_current_screen = current_screen
            # Update log context so every line emitted from this overlay tick
            # (and downstream task ticks until refreshed) carries the current
            # player + FSM node.
            set_log_context(
                player=active_player or "",
                node=current_screen or "",
            )

            # Resolve regions against the active player's state so screen-version `cond`
            # picks the right `_vN` override per account (otherwise the worker would always
            # match v1 boxes regardless of player progression).
            state_flat: dict[str, Any] | None = None
            if active_player:
                try:
                    from config.state_store import get_state_store

                    state_flat = (
                        get_state_store().get_or_create(active_player).to_flat_dict()
                    )
                except Exception:
                    logger.debug(
                        "overlay analyze: state_flat lookup failed for player=%s",
                        active_player,
                        exc_info=True,
                    )

            # Per-player TTL state: pick (or create) the sub-dict for the
            # current ``active_player``. Empty string keys "no active player"
            # — pre-identity / device-level ticks land there.
            tt_key = (active_player or "").strip()
            player_state = self._overlay_rule_eval_state_by_player.setdefault(
                tt_key, {}
            )
            if self._redis is not None:
                cached_rev = self._overlay_ttl_rev_by_player.get(tt_key, "0")
                last_sync = self._overlay_ttl_last_sync_mono_by_player.get(tt_key, 0.0)
                rev, last_sync = await sync_overlay_ttl_state_if_needed(
                    self._redis,
                    instance_id=self._cfg.instance_id,
                    player_id=tt_key,
                    rule_eval_state=player_state,
                    cached_rev=cached_rev,
                    last_sync_mono=last_sync,
                )
                self._overlay_ttl_rev_by_player[tt_key] = rev
                self._overlay_ttl_last_sync_mono_by_player[tt_key] = last_sync
            results = await run_overlay_analysis(
                image_bgr,
                repo_root=root,
                current_screen=current_screen,
                rule_eval_state=player_state,
                state_flat=state_flat,
                ocr_client=self._ocr_client,
                device_level_only=device_level_only,
                module_scope=test_module,
                instance_id=self._cfg.instance_id,
                redis_async=self._redis,
            )
        except Exception:
            logger.exception("overlay analyze failed on %s", self._cfg.instance_id)
            return
        # Mirror the in-memory TTL snapshot to Redis (wall-clock seconds) so
        # the wiki/analyze UI can render "last evaluated" / "next eval in"
        # per-rule, per-player. ``rule_eval_state`` holds ``time.monotonic()``
        # values — convert to ``time.time()`` so cross-process readers can
        # compute absolute "X seconds ago" without sharing the worker clock.
        await self._persist_overlay_ttl_snapshot(tt_key, player_state)
        await self._schedule_overlay_matches(results, active_player=active_player)
        await self._maybe_dismiss_unknown_popup(
            results, current_screen=current_screen
        )

    async def _maybe_dismiss_unknown_popup(
        self,
        overlay_results: dict[str, object],
        *,
        current_screen: str | None,
    ) -> None:
        """Tap ``claim_button_close`` when stuck on an unrecognized screen.

        Fires only when (a) current_screen is hard-cleared to None, (b) the
        worker has been in that state for >= 10s, and (c) no global overlay
        rule matched this tick — i.e. ad/popup analyzers without a node
        binding produced nothing. A 10s Redis NX-EX lock acts as a retry
        backoff: if the dismiss scenario runs but the screen stays unknown
        (unknown→unknown is not a transition, so `_drop_pending_…` won't
        clear the lock), the TTL is the only thing that lets a future
        attempt re-arm.
        """
        if current_screen:
            return
        unknown_since = float(getattr(self, "_unknown_since", 0.0) or 0.0)
        if unknown_since <= 0.0:
            return
        if (time.monotonic() - unknown_since) < 10.0:
            return
        for payload in overlay_results.values():
            if isinstance(payload, dict) and payload.get("matched"):  # ty: ignore[invalid-argument-type]
                return
        if self._queue is None:
            return
        lock_key = (
            f"wos:instance:{self._cfg.instance_id}:dismiss_unknown_popup_lock"
        )
        if self._redis is not None:
            try:
                acquired = bool(
                    await self._redis.set(lock_key, "1", nx=True, ex=10)
                )
            except Exception:
                logger.debug(
                    "dismiss_unknown_popup: lock acquire failed; allowing push",
                    exc_info=True,
                )
                acquired = True
            if not acquired:
                dismiss_unknown_popup_counter().add(
                    1,
                    attributes={
                        "instance_id": self._cfg.instance_id,
                        "outcome": "locked",
                    },
                )
                return
        import uuid as _uuid

        task_id = (
            f"ovl:{self._cfg.instance_id}:dismiss_unknown_popup:"
            f"{_uuid.uuid4().hex[:8]}"
        )
        try:
            await self._queue.schedule(
                task_id=task_id,
                player_id="",
                task_type="dismiss_unknown_popup",
                priority=70_000,
                run_at=time.time(),
                instance_id=self._cfg.instance_id,
                skip_if_duplicate=True,
                dedup_ignore_region=True,
            )
            logger.info(
                "[fallback] %s: dismiss_unknown_popup enqueued "
                "(unknown for %.1fs, no global match)",
                self._cfg.instance_id,
                time.monotonic() - unknown_since,
            )
            dismiss_unknown_popup_counter().add(
                1,
                attributes={
                    "instance_id": self._cfg.instance_id,
                    "outcome": "enqueued",
                },
            )
        except Exception:
            logger.debug(
                "dismiss_unknown_popup: schedule failed", exc_info=True
            )
            dismiss_unknown_popup_counter().add(
                1,
                attributes={
                    "instance_id": self._cfg.instance_id,
                    "outcome": "error",
                },
            )

    async def _drop_pending_dismiss_unknown_popup(self, detected_screen: str) -> None:
        """On unknown → known transition, evict the stale fallback from the queue.

        ``remove_by_task_type`` only touches ZSET members, so any in-flight
        ``dismiss_unknown_popup`` already claimed by a worker keeps running.
        The NX-lock key is also cleared so a future unknown-dwell can re-arm
        the fallback without waiting out the 10s TTL.
        """
        if self._queue is None:
            return
        with contextlib.suppress(Exception):
            removed = await self._queue.remove_by_task_type(
                "dismiss_unknown_popup", self._cfg.instance_id
            )
            if removed:
                logger.info(
                    "%s: dropped %d stale dismiss_unknown_popup "
                    "(screen=%s detected after unknown)",
                    self._cfg.instance_id,
                    removed,
                    detected_screen,
                )
                dismiss_unknown_popup_counter().add(
                    int(removed),
                    attributes={
                        "instance_id": self._cfg.instance_id,
                        "outcome": "dropped_on_recovery",
                    },
                )
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(
                    f"wos:instance:{self._cfg.instance_id}"
                    ":dismiss_unknown_popup_lock"
                )

    async def _persist_overlay_ttl_snapshot(
        self, player_id: str, player_state: dict[str, float]
    ) -> None:
        if self._redis is None:
            return
        last_persist = self._overlay_ttl_last_persist_mono_by_player.get(player_id)
        new_persist = await maybe_persist_overlay_ttl_state_to_redis(
            self._redis,
            instance_id=self._cfg.instance_id,
            player_id=player_id,
            rule_eval_state=player_state,
            last_persist_mono=last_persist,
        )
        if new_persist is not None:
            self._overlay_ttl_last_persist_mono_by_player[player_id] = new_persist


class InstanceWorkerScreenDetectMixin(_Base):
    """Extracted screen detection + Redis `current_screen` persistence."""

    _cfg: Any
    _redis: Any
    _screen_detector: Any
    _last_detected_screen: str | None
    _last_detected_screen_at: float
    _unknown_since: float
    _screen_unknown_streak: int

    _SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES: int
    _SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS: float

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        # The detector's own OCR/landmark logs would otherwise inherit the
        # previous tick's `node` from log context and read as if the new
        # screen had already been confirmed. Clear it for the duration of
        # detection so those lines render as `[inst/player/-]` — and only
        # restore once we have a verdict.
        set_log_context(node="")
        # Sticky hint: when we know what we were on last tick, the detector
        # first checks ONLY that screen's verify rules. On the steady-state
        # case where the bot dwells on one screen for many ticks this skips
        # the full multi-screen scan; on a miss the detector falls back to
        # the global pipeline so worst-case cost is unchanged.
        sticky_hint = self._last_detected_screen or None
        nav_expected: str | None = None
        if self._redis is not None:
            try:
                raw = await self._redis.hget(
                    f"wos:instance:{self._cfg.instance_id}:state",
                    "nav_expected_screen",
                )
                if raw is not None:
                    nav_expected = (
                        raw.decode() if isinstance(raw, bytes) else str(raw)
                    ).strip() or None
            except Exception:
                logger.debug(
                    "Screen detect: nav_expected_screen read failed for %s",
                    self._cfg.instance_id,
                    exc_info=True,
                )
        try:
            detected = await self._screen_detector.detect_screen(
                image_bgr,
                hint=sticky_hint,
                expected=nav_expected,
            )
        except Exception:
            logger.debug(
                "Screen detect failed for %s",
                self._cfg.instance_id,
                exc_info=True,
            )
            set_log_context(node=self._last_detected_screen or "")
            return self._last_detected_screen

        if detected != ScreenName.UNKNOWN:
            detected_s = str(detected)
            self._last_detected_screen = detected_s
            self._last_detected_screen_at = time.monotonic()
            was_unknown = self._unknown_since > 0.0
            self._unknown_since = 0.0
            self._screen_unknown_streak = 0
            if self._redis is not None:
                with contextlib.suppress(Exception):
                    await self._redis.hset(
                        f"wos:instance:{self._cfg.instance_id}:state",
                        "current_screen",
                        detected_s,
                    )
            if was_unknown:
                await self._drop_pending_dismiss_unknown_popup(detected_s)
            self._note_boot_interactive_screen(detected_s)
            set_log_context(node=detected_s)
            return detected_s

        self._screen_unknown_streak += 1
        age = time.monotonic() - self._last_detected_screen_at
        should_clear = (
            self._screen_unknown_streak >= int(self._SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES)
            or self._last_detected_screen_at <= 0
            or age >= float(self._SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS)
        )
        if not should_clear:
            set_log_context(node=self._last_detected_screen or "")
            return self._last_detected_screen

        self._last_detected_screen = None
        self._last_detected_screen_at = 0.0
        # Start the unknown-dwell timer at the moment we hard-clear — the
        # popup-dismiss fallback (>= 10s) measures from this point, not from
        # the soft-unknown sticky window.
        if self._unknown_since <= 0.0:
            self._unknown_since = time.monotonic()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.hset(
                    f"wos:instance:{self._cfg.instance_id}:state",
                    "current_screen",
                    "",
                )
        return None

