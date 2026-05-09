from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from analysis.overlay import run_overlay_analysis
from navigation.detector import ScreenName

logger = logging.getLogger(__name__)


class InstanceWorkerScreenMixin:
    _cfg: Any
    _redis: Any
    _bot_actions: Any
    _screen_detector: Any
    _overlay_rule_eval_state: dict[str, float]
    _last_current_screen: str | None
    _last_detected_screen: str | None
    _last_detected_screen_at: float
    _screen_unknown_streak: int

    async def _schedule_overlay_matches(self, overlay_results: dict[str, object]) -> None:
        raise NotImplementedError

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        raise NotImplementedError

    def _grab_layout_bgr(self) -> np.ndarray:
        return self._bot_actions.capture_screen_bgr(self._cfg.instance_id)

    async def _overlay_analyze_bgr(
        self,
        image_bgr: np.ndarray,
        *,
        current_screen_override: str | None = None,
    ) -> None:
        """Run ``analyze/analyze.yaml`` overlay rules on an ADB frame (BGR)."""
        repo_root = Path(__file__).resolve().parent.parent
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

            results = await run_overlay_analysis(
                image_bgr,
                repo_root=repo_root,
                current_screen=current_screen,
                rule_eval_state=self._overlay_rule_eval_state,
                state_flat=state_flat,
            )
        except Exception:
            logger.exception("overlay analyze failed on %s", self._cfg.instance_id)
            return
        await self._schedule_overlay_matches(results)


class InstanceWorkerScreenDetectMixin:
    """Extracted screen detection + Redis `current_screen` persistence."""

    _cfg: Any
    _redis: Any
    _screen_detector: Any
    _last_detected_screen: str | None
    _last_detected_screen_at: float
    _screen_unknown_streak: int

    _SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES: int
    _SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS: float

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        try:
            detected = await self._screen_detector.detect_screen(image_bgr)
        except Exception:
            logger.debug(
                "Screen detect failed for %s",
                self._cfg.instance_id,
                exc_info=True,
            )
            return self._last_detected_screen

        if detected != ScreenName.UNKNOWN:
            detected_s = str(detected)
            self._last_detected_screen = detected_s
            self._last_detected_screen_at = time.monotonic()
            self._screen_unknown_streak = 0
            if self._redis is not None:
                with contextlib.suppress(Exception):
                    await self._redis.hset(
                        f"wos:instance:{self._cfg.instance_id}:state",
                        "current_screen",
                        detected_s,
                    )
            return detected_s

        self._screen_unknown_streak += 1
        age = time.monotonic() - self._last_detected_screen_at
        should_clear = (
            self._screen_unknown_streak >= int(self._SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES)
            or self._last_detected_screen_at <= 0
            or age >= float(self._SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS)
        )
        if not should_clear:
            return self._last_detected_screen

        self._last_detected_screen = None
        self._last_detected_screen_at = 0.0
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.hset(
                    f"wos:instance:{self._cfg.instance_id}:state",
                    "current_screen",
                    "",
                )
        return None

