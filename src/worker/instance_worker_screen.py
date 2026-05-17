from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from analysis.overlay import run_overlay_analysis
from config.log_context import set_log_context
from config.paths import repo_root
from navigation.detector import ScreenName

logger = logging.getLogger(__name__)



if TYPE_CHECKING:
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
        return self._bot_actions.capture_screen_bgr_adb(self._cfg.instance_id)

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
                row = await self._redis.hgetall(
                    f"wos:instance:{self._cfg.instance_id}:state"
                )
                if row:
                    decoded = {
                        (k.decode() if isinstance(k, bytes) else str(k)):
                            (v.decode() if isinstance(v, bytes) else str(v))
                        for k, v in row.items()
                    }
                    if current_screen is None:
                        cur = decoded.get("current_screen", "").strip()
                        current_screen = cur or None
                    ap = decoded.get("active_player", "").strip()
                    active_player = ap or None

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
            results = await run_overlay_analysis(
                image_bgr,
                repo_root=root,
                current_screen=current_screen,
                rule_eval_state=player_state,
                state_flat=state_flat,
                ocr_client=self._ocr_client,
                device_level_only=device_level_only,
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
        binding produced nothing. A 30s Redis NX-EX lock keeps the scenario
        from re-enqueueing on every 1 Hz rolling tick while it runs.
        """
        if current_screen:
            return
        unknown_since = float(getattr(self, "_unknown_since", 0.0) or 0.0)
        if unknown_since <= 0.0:
            return
        if (time.monotonic() - unknown_since) < 10.0:
            return
        for payload in overlay_results.values():
            if isinstance(payload, dict) and payload.get("matched"):
                return
        if self._queue is None:
            return
        lock_key = (
            f"wos:instance:{self._cfg.instance_id}:dismiss_unknown_popup_lock"
        )
        if self._redis is not None:
            try:
                acquired = bool(
                    await self._redis.set(lock_key, "1", nx=True, ex=30)
                )
            except Exception:
                logger.debug(
                    "dismiss_unknown_popup: lock acquire failed; allowing push",
                    exc_info=True,
                )
                acquired = True
            if not acquired:
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
        except Exception:
            logger.debug(
                "dismiss_unknown_popup: schedule failed", exc_info=True
            )

    async def _persist_overlay_ttl_snapshot(
        self, player_id: str, player_state: dict[str, float]
    ) -> None:
        if self._redis is None or not player_state:
            return
        now_wall = time.time()
        now_mono = time.monotonic()
        mapping: dict[str, str] = {}
        for rule_name, mono_ts in player_state.items():
            try:
                wall_ts = now_wall - (now_mono - float(mono_ts))
            except (TypeError, ValueError):
                continue
            mapping[str(rule_name)] = f"{wall_ts:.3f}"
        if not mapping:
            return
        # ``player_id`` may be empty when no active_player is set; route those
        # to a dedicated key so they don't pollute any real player's state.
        key = (
            f"wos:player:{player_id}:overlay_ttl"
            if player_id
            else f"wos:instance:{self._cfg.instance_id}:overlay_ttl_anon"
        )
        try:
            await self._redis.hset(key, mapping=mapping)
        except Exception:
            logger.debug(
                "overlay TTL snapshot write failed key=%s",
                key,
                exc_info=True,
            )


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
        try:
            detected = await self._screen_detector.detect_screen(
                image_bgr, hint=sticky_hint
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
            self._unknown_since = 0.0
            self._screen_unknown_streak = 0
            if self._redis is not None:
                with contextlib.suppress(Exception):
                    await self._redis.hset(
                        f"wos:instance:{self._cfg.instance_id}:state",
                        "current_screen",
                        detected_s,
                    )
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

