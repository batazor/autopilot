"""Loop-until-clear pop-up handler, decoupled from any event framework.

The original spec targeted an ``events`` layer (``EventHandleResult``,
``HandleContext``, ``PopupBlockingHandler``) that does not exist in this repo.
This handler keeps the same loop-until-clear semantics and safety rules but
depends only on duck-typed protocols and a local result enum, so it is
unit-testable today and trivial to wire into a runtime later.

Safety rules enforced here:

- ``PURCHASE`` may only be dismissed via the close point (the X / its geometric
  fallback). A purchase CTA is never tapped. If no close point exists, escalate
  rather than guess.
- ``CAPTCHA`` is routed to the captcha handler, never dismissed. With no captcha
  handler wired, it escalates.
- There is intentionally **no** blind "tap outside to dismiss": a stray tap on a
  map/city screen issues a real in-game action. The detector always supplies a
  geometric close point for a confirmed native modal, so the X path covers it;
  anything without a close point escalates.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Protocol

from popup.models import PopupKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import numpy as np

    from layout.types import Point
    from popup.detector import PopupDetector

logger = logging.getLogger(__name__)


class PopupHandleResult(StrEnum):
    """Outcome of a handle pass."""

    HANDLED = auto()  # screen is clear (no modal remains)
    CAPTCHA_ROUTED = auto()  # handed to the captcha handler
    ESCALATE = auto()  # still blocked / unsafe to act → caller recovers


class TapActions(Protocol):
    """Minimal slice of ``adb.BotActions`` this handler needs."""

    def tap(self, instance_id: str, point: Point) -> bool: ...


@dataclass(frozen=True, slots=True)
class HandlerConfig:
    max_layers: int = 4  # pop-ups stack; bound the dismiss loop
    settle_s: float = 0.6  # let the dismiss animation finish before re-checking


_DEFAULT_CONFIG = HandlerConfig()


class PopupBlockingHandler:
    """Dismiss stacked pop-ups until the screen is clear or escalation is due."""

    def __init__(
        self,
        detector: PopupDetector,
        capture: Callable[[str], np.ndarray],
        *,
        config: HandlerConfig | None = None,
        captcha_handler: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._detector = detector
        self._capture = capture
        self._cfg = config or _DEFAULT_CONFIG
        self._captcha_handler = captcha_handler

    async def handle(self, instance_id: str, actions: TapActions) -> PopupHandleResult:
        """Loop-until-clear with safe escalation. Returns the final outcome."""
        for attempt in range(self._cfg.max_layers):
            image = self._capture(instance_id)
            state = await self._detector.detect(image)

            if state.kind == PopupKind.NONE:
                return PopupHandleResult.HANDLED

            if state.kind == PopupKind.PAGE:
                logger.info("popup: known page %r on %s — deferring", state.screen_name or "-", instance_id)
                return PopupHandleResult.ESCALATE

            if state.kind == PopupKind.CAPTCHA:
                return await self._route_captcha(instance_id)

            if state.kind == PopupKind.PURCHASE:
                # The ONLY permitted tap is the close point. Never the CTA.
                if state.close_point is None:
                    logger.warning("popup: purchase modal with no close point on %s — escalating", instance_id)
                    return PopupHandleResult.ESCALATE
                actions.tap(instance_id, state.close_point)
            elif (
                state.kind in (PopupKind.REWARD_CLAIM, PopupKind.TAP_TO_CONTINUE)
                and state.primary_point is not None
            ):
                # REWARD_CLAIM → the claim button; TAP_TO_CONTINUE → the center
                # ("tap anywhere"). Both are the card's primary point, never the X.
                actions.tap(instance_id, state.primary_point)
            elif state.close_point is not None:
                actions.tap(instance_id, state.close_point)  # prefer the X
            else:
                # Overlay present but no actionable point (e.g. ad/webview with
                # no learned model yet). Blind taps are unsafe — escalate.
                logger.info("popup: %s with no close point on %s — escalating", state.kind, instance_id)
                return PopupHandleResult.ESCALATE

            logger.debug("popup: dismissed layer %d (%s) on %s", attempt + 1, state.kind, instance_id)
            await asyncio.sleep(self._cfg.settle_s)  # let animation finish, then re-check

        # Still blocked after max_layers → recovery.
        return PopupHandleResult.ESCALATE

    async def _route_captcha(self, instance_id: str) -> PopupHandleResult:
        if self._captcha_handler is None:
            logger.warning("popup: captcha detected on %s but no captcha handler wired — escalating", instance_id)
            return PopupHandleResult.ESCALATE
        await self._captcha_handler(instance_id)
        return PopupHandleResult.CAPTCHA_ROUTED
