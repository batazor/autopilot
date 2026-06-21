"""``exec: dismiss_popup`` — layered popup dismissal via the popup detector."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from popup.detector import PopupDetector
from popup.models import PopupKind, PopupState
from tasks import dsl_runtime

if TYPE_CHECKING:
    from layout.types import Point
    from tasks.dsl_exec.context import (
        DslExecContext,
    )

logger = logging.getLogger(__name__)

_DISMISS_POPUP_MAX_LAYERS = 4
# Let the dismiss animation finish before re-capturing, so the next detect
# pass sees the screen *behind* the modal rather than the one we just tapped.
_DISMISS_POPUP_SETTLE_S = 0.6


def _popup_tap_target(state: PopupState) -> tuple[Point, str] | None:
    """Resolve the one safe tap for ``state``: ``(point, approval_region)``.

    Mirrors the worker rolling-loop dispatch in
    :meth:`worker.instance_worker_screen.InstanceWorkerScreenMixin._maybe_handle_popup`
    so the scenario fallback and the live tick agree on what is safe to tap per
    kind. Returns ``None`` when there is no safe action — the caller stops and
    lets the legacy region shotgun take over.
    """
    if state.kind == PopupKind.PAGE:
        return None
    if state.kind == PopupKind.TAP_TO_CONTINUE and state.primary_point is not None:
        # "Tap anywhere / tap to continue" page — the card center, never a
        # (non-existent) top-right X.
        return state.primary_point, "popup_tap_anywhere"
    if state.kind == PopupKind.REWARD_CLAIM and state.primary_point is not None:
        return state.primary_point, "popup_close"
    # SAFE_DISMISS / UNKNOWN_MODAL / PURCHASE / AD_WEBVIEW → the X only. For
    # PURCHASE this is the *only* permitted tap; a Buy/Spend CTA is never tapped.
    if state.close_point is not None:
        return state.close_point, "popup_close"
    return None


async def _exec_dismiss_popup(ctx: DslExecContext) -> None:
    """Dismiss stacked modals with the template-free pop-up detector.

    Runs :class:`popup.detector.PopupDetector` in a loop-until-clear: each pass
    localizes a blurred-scrim modal, safety-classifies it, and issues a single
    approval-gated tap on the resolved target (the X, a reward Claim, or a
    "tap to continue" center). Repeats until the frame is clear, a captcha is
    seen, the detector escalates (ad/webview with no learned close model), a
    tap is rejected, or ``max_layers`` is reached.

    This is the smart front-half of ``dismiss_unknown_popup.yaml``; the region
    shotgun in that scenario remains the deeper fallback when the detector
    finds nothing actionable or escalates.

    Args (sibling YAML keys on the ``exec:`` step):
    * ``max_layers`` — cap on stacked dismissals (default
      ``_DISMISS_POPUP_MAX_LAYERS``).
    """
    args = ctx.args or {}
    try:
        max_layers = int(args.get("max_layers", _DISMISS_POPUP_MAX_LAYERS))
    except (TypeError, ValueError):
        max_layers = _DISMISS_POPUP_MAX_LAYERS
    max_layers = max(1, max_layers)

    actions = dsl_runtime.bot_actions()
    detector = PopupDetector(dsl_runtime.ocr_client())

    dismissed = 0
    for layer in range(max_layers):
        # A previous iteration tapped (closing a modal); drop the cache so this
        # pass detects against a freshly-captured, post-animation frame instead
        # of re-reading the modal we just dismissed.
        if layer > 0:
            with contextlib.suppress(Exception):
                actions.invalidate_frame_cache(ctx.instance_id)
        try:
            image = await asyncio.to_thread(
                actions.capture_screen_bgr, ctx.instance_id
            )
        except Exception:
            ctx.result.update(
                {
                    "reason": "capture_failed",
                    "popup_action": "error",
                }
            )
            logger.exception(
                "dsl exec dismiss_popup: capture failed instance=%s",
                ctx.instance_id,
            )
            return

        try:
            state = await detector.detect(image)
        except Exception:
            ctx.result.update(
                {
                    "reason": "detect_failed",
                    "popup_action": "error",
                }
            )
            logger.exception(
                "dsl exec dismiss_popup: detect failed instance=%s",
                ctx.instance_id,
            )
            return

        if state.kind == PopupKind.NONE:
            ctx.result.update(
                {
                    "reason": "no_popup",
                    "popup_action": "clear",
                    "popup_dismissed": dismissed,
                }
            )
            logger.info(
                "dsl exec dismiss_popup: instance=%s clear after %d dismissal(s)",
                ctx.instance_id,
                dismissed,
            )
            return

        if state.kind == PopupKind.PAGE:
            screen_name = (state.screen_name or "").strip()
            ctx.result.update(
                {
                    "reason": "screen_page",
                    "popup_action": "defer_to_screen",
                    "popup_kind": state.kind.value,
                    "popup_screen": screen_name,
                    "popup_dismissed": dismissed,
                }
            )
            if ctx.redis_client is not None and screen_name:
                try:
                    await ctx.redis_client.hset(
                        f"wos:instance:{ctx.instance_id}:state",
                        "current_screen",
                        screen_name,
                    )
                except Exception:
                    logger.debug(
                        "dsl exec dismiss_popup: failed to persist detected page",
                        exc_info=True,
                    )
            logger.info(
                "dsl exec dismiss_popup: %s is page %r — deferring to screen automation",
                ctx.instance_id,
                screen_name or "-",
            )
            return

        if state.kind == PopupKind.CAPTCHA:
            ctx.result.update(
                {
                    "reason": "captcha",
                    "popup_action": "blocked",
                    "popup_kind": state.kind.value,
                    "popup_dismissed": dismissed,
                }
            )
            # No worker-side solver — never tap into a captcha. Stop so the
            # region shotgun cannot blindly tap it either.
            logger.info(
                "dsl exec dismiss_popup: captcha on %s — not dismissing",
                ctx.instance_id,
            )
            return

        target = _popup_tap_target(state)
        if target is None:
            ctx.result.update(
                {
                    "reason": "no_safe_tap",
                    "popup_action": "escalated",
                    "popup_kind": state.kind.value,
                    "popup_dismissed": dismissed,
                }
            )
            # Overlay present but no safe point (ad/webview without a learned
            # close model). Escalate to the region shotgun, which carries
            # tap-anywhere candidates the geometric heuristic lacks.
            logger.info(
                "dsl exec dismiss_popup: %s on %s with no safe tap — "
                "escalating to region shotgun",
                state.kind.value,
                ctx.instance_id,
            )
            return

        point, approval_region = target
        ctx.result.update(
            {
                "popup_action": "tap_pending",
                "popup_kind": state.kind.value,
                "popup_tap_x": point.x,
                "popup_tap_y": point.y,
                "popup_approval_region": approval_region,
                "popup_dismissed": dismissed,
            }
        )
        try:
            tapped = bool(
                await asyncio.to_thread(
                    actions.tap,
                    ctx.instance_id,
                    point,
                    approval_region=approval_region,
                    approval_source="popup",
                )
            )
        except Exception:
            ctx.result.update(
                {
                    "reason": "tap_failed",
                    "popup_action": "error",
                    "popup_kind": state.kind.value,
                    "popup_tap_x": point.x,
                    "popup_tap_y": point.y,
                    "popup_approval_region": approval_region,
                    "popup_dismissed": dismissed,
                }
            )
            logger.exception(
                "dsl exec dismiss_popup: tap failed at (%d,%d) instance=%s",
                point.x,
                point.y,
                ctx.instance_id,
            )
            return
        if not tapped:
            ctx.result.update(
                {
                    "reason": "tap_rejected",
                    "popup_action": "blocked",
                    "popup_kind": state.kind.value,
                    "popup_tap_x": point.x,
                    "popup_tap_y": point.y,
                    "popup_approval_region": approval_region,
                    "popup_dismissed": dismissed,
                }
            )
            # Operator rejected the approval (or the slot is busy). Bail so the
            # "no" actually stops the loop instead of re-prompting every layer.
            logger.info(
                "dsl exec dismiss_popup: tap at (%d,%d) blocked/rejected on %s "
                "— aborting (dismissed=%d)",
                point.x,
                point.y,
                ctx.instance_id,
                dismissed,
            )
            return

        dismissed += 1
        ctx.result.update(
            {
                "popup_action": "tapped",
                "popup_kind": state.kind.value,
                "popup_tap_x": point.x,
                "popup_tap_y": point.y,
                "popup_approval_region": approval_region,
                "popup_dismissed": dismissed,
            }
        )
        logger.info(
            "dsl exec dismiss_popup: instance=%s layer=%d kind=%s tap=(%d,%d)",
            ctx.instance_id,
            layer + 1,
            state.kind.value,
            point.x,
            point.y,
        )
        await asyncio.sleep(_DISMISS_POPUP_SETTLE_S)

    ctx.result.update(
        {
            "reason": "max_layers",
            "popup_action": "max_layers",
            "popup_dismissed": dismissed,
        }
    )
    logger.info(
        "dsl exec dismiss_popup: instance=%s reached max-layers cap (%d) — stopping",
        ctx.instance_id,
        max_layers,
    )
