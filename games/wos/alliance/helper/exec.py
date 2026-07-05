"""DSL exec for the alliance-help bubble — fresh-frame tap + chat escape.

Why not a plain ``click:`` step: the overlay engine matches the bubble on a
rolling frame that can be seconds old. The bubble disappears the moment another
member helps first, and the same screen spot then hosts the chat entry — so the
blind click landed in alliance chat and the bot sat there. This handler ports
the standalone ``play-helper`` loop's proven detect→tap→chat-escape logic into
the worker:

1. **Chat escape first**: if the chat title ("Chat" / "Чат") is visible, we
   already slipped in — tap the back arrow and stop.
2. **Fresh re-match**: match the help icon on a frame captured NOW; a bubble
   that vanished since the overlay tick is a clean no-op, not a mis-tap.
3. **Post-tap verify**: after a real tap, re-capture; if chat opened anyway
   (bubble expired mid-flight), tap back immediately.

Taps go through ``BotActions.tap`` so click-approval mode keeps gating them.
Region bboxes are copied verbatim from the modules' ``area.yaml`` (cited
inline), matching the standalone clicker.
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from layout.types import Point
from tasks import dsl_runtime

if TYPE_CHECKING:
    import numpy as np

    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# ── Regions (percent of 720x1280), verbatim from the modules' area.yaml ──────
# games/wos/alliance/helper/area.yaml → button.alliance.help
_HELP_BBOX = {"x": 69.6357391761772, "y": 85.98686358461053,
              "width": 7.109658277834156, "height": 5.253318747044119}
# games/wos/chat/area.yaml → chat.title (EN "Chat" / RU "Чат")
_CHAT_TITLE_BBOX = {"x": 11.687141209950434, "y": 0.9764946866410164,
                    "width": 13.525280898876392, "height": 4.215132897460327}
# games/wos/core/chief_profile/area.yaml → icon.page.back (blind-tap pattern)
_BACK_BBOX = {"x": 0.7702702702702703, "y": 0.5163043478260869,
              "width": 10.832046332046332, "height": 4.95}

_HELP_CROP = "games/wos/alliance/helper/references/crop/alliance.helper_button.alliance.help.png"
_CHAT_TITLE_CROP_EN = "games/wos/chat/references/crop/chat.alliance_chat.title.png"
_CHAT_REF_RU = "games/wos/ru/chat/references/chat.alliance.png"  # full frame; title cropped below

# Fallback-only re-match floor (no overlay coordinates to trust, e.g. a manual
# run). The handshake icon ANIMATES (it swings): live scores on a plainly
# visible bubble ranged 0.56-0.94 across its cycle while an absent bubble
# scored ~0.6, so no threshold separates them reliably — which is why the
# primary path taps the overlay's own match coordinates instead of re-matching,
# with the chat-escape verify as the staleness safety net.
_HELP_THRESHOLD = 0.65
_CHAT_THRESHOLD = 0.80
_SEARCH_PAD_PCT = 6.0
_POST_TAP_SETTLE_S = 1.2

# Fallback sampling across a full swing cycle (~2.4 s) — the standalone
# play-helper loop got this for free from its 2 Hz cadence.
_MATCH_ATTEMPTS = 6
_MATCH_RETRY_DELAY_S = 0.4


def _expand(bbox: dict[str, float], pad: float = _SEARCH_PAD_PCT) -> dict[str, float]:
    return {
        "x": max(0.0, bbox["x"] - pad),
        "y": max(0.0, bbox["y"] - pad),
        "width": min(100.0, bbox["width"] + 2 * pad),
        "height": min(100.0, bbox["height"] + 2 * pad),
    }


def _crop_bbox_px(img: np.ndarray, bbox: dict[str, float]) -> np.ndarray:
    h, w = img.shape[:2]
    x0 = int(bbox["x"] * w / 100.0)
    y0 = int(bbox["y"] * h / 100.0)
    return img[y0:y0 + int(bbox["height"] * h / 100.0), x0:x0 + int(bbox["width"] * w / 100.0)]


def _bbox_center(bbox: dict[str, float], w: int, h: int) -> Point:
    return Point(
        int((bbox["x"] + bbox["width"] / 2) * w / 100.0),
        int((bbox["y"] + bbox["height"] / 2) * h / 100.0),
    )


@lru_cache(maxsize=1)
def _templates() -> dict[str, Any]:
    """Help crop + chat-title crops (EN + RU), loaded once per worker process.

    Missing catalog assets are skipped (an EN-only install has no RU reference);
    at least the help crop must exist.
    """
    import cv2

    from config.paths import repo_root

    root = repo_root()
    help_tpl = cv2.imread(str(root / _HELP_CROP))
    if help_tpl is None:
        msg = f"help crop not found: {root / _HELP_CROP}"
        raise FileNotFoundError(msg)
    chat_titles: list[Any] = []
    en = cv2.imread(str(root / _CHAT_TITLE_CROP_EN))
    if en is not None:
        chat_titles.append(en)
    ru_ref = cv2.imread(str(root / _CHAT_REF_RU))
    if ru_ref is not None:
        chat_titles.append(_crop_bbox_px(ru_ref, _CHAT_TITLE_BBOX))
    return {"help": help_tpl, "chat_titles": chat_titles}


def _match(
    frame: np.ndarray, template: np.ndarray, bbox: dict[str, float], threshold: float
) -> tuple[bool, float, Point]:
    from layout.template_match import match_template_in_search_roi_bbox_percent

    res = match_template_in_search_roi_bbox_percent(
        frame, template, _expand(bbox), threshold=threshold
    )
    score = float(res.get("score") or 0.0)
    tx, ty = res.get("top_left", (0, 0))
    center = Point(
        int(tx) + int(res.get("template_w", template.shape[1])) // 2,
        int(ty) + int(res.get("template_h", template.shape[0])) // 2,
    )
    return score >= threshold, score, center


def _chat_score(frame: np.ndarray) -> float:
    """Best chat-title match across the EN/RU crops (0.0 when none loaded)."""
    best = 0.0
    for tpl in _templates()["chat_titles"]:
        _, score, _ = _match(frame, tpl, _CHAT_TITLE_BBOX, _CHAT_THRESHOLD)
        best = max(best, score)
    return best


async def _capture(actions: Any, instance_id: str) -> np.ndarray | None:
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    except Exception:
        logger.exception("play_helper: capture failed instance=%s", instance_id)
        return None


async def _tap_back(actions: Any, ctx: DslExecContext, frame: np.ndarray) -> bool:
    h, w = frame.shape[:2]
    return await asyncio.to_thread(
        actions.tap,
        ctx.instance_id,
        _bbox_center(_BACK_BBOX, w, h),
        approval_region="icon.page.back",
        approval_context={"source": "play_helper.chat_escape"},
        require_approval=False,
    )


async def _overlay_match_center(ctx: DslExecContext, frame: np.ndarray) -> Point | None:
    """The overlay's own match point for THIS pushed task (from instance state).

    The worker publishes the queue item's ``tap_match_*_pct`` — i.e. where the
    overlay engine actually saw the bubble — for the duration of the task.
    Trusting it beats re-matching: the swinging icon defeats any fixed template
    threshold, while the overlay only pushes on a confident hit.
    """
    if ctx.redis_client is None:
        return None
    try:
        raw = await ctx.redis_client.hmget(
            f"wos:instance:{ctx.instance_id}:state",
            ["current_task_tap_match_x_pct", "current_task_tap_match_y_pct"],
        )
    except Exception:
        return None
    try:
        vals = [float((v.decode() if isinstance(v, bytes) else v) or "") for v in raw]
    except (TypeError, ValueError):
        return None
    h, w = frame.shape[:2]
    return Point(int(vals[0] / 100.0 * w), int(vals[1] / 100.0 * h))


async def _exec_play_helper(ctx: DslExecContext) -> None:
    """Tap the alliance-help bubble; escape chat on a slip."""
    actions = dsl_runtime.bot_actions()
    frame = await _capture(actions, ctx.instance_id)
    if frame is None:
        ctx.result.update({"action": "capture_failed"})
        return

    # 1) Already in chat (a previous mis-tap, or any other path) → back out.
    chat = _chat_score(frame)
    if chat >= _CHAT_THRESHOLD:
        tapped = await _tap_back(actions, ctx, frame)
        ctx.result.update(
            {"action": "chat_escape" if tapped else "chat_escape_blocked", "chat_score": round(chat, 3)}
        )
        return

    # 2) Primary: tap where the overlay matched when it pushed this task.
    #    Fallback (no coords → e.g. a manual enqueue): sample frames across the
    #    swing animation and tap the best fresh match.
    center = await _overlay_match_center(ctx, frame)
    best_score = -1.0
    if center is None:
        hit = False
        for attempt in range(_MATCH_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_MATCH_RETRY_DELAY_S)
                nxt = await _capture(actions, ctx.instance_id)
                if nxt is not None:
                    frame = nxt
            hit, score, center = _match(frame, _templates()["help"], _HELP_BBOX, _HELP_THRESHOLD)
            best_score = max(best_score, score)
            if hit:
                break
        if not hit:
            ctx.result.update({"action": "vanished", "score": round(best_score, 3)})
            return

    tapped = await asyncio.to_thread(
        actions.tap,
        ctx.instance_id,
        center,
        approval_region="button.alliance.help",
        approval_context={"score": round(best_score, 4), "source": "play_helper"},
        require_approval=False,
    )
    if not tapped:
        ctx.result.update({"action": "tap_blocked", "score": round(best_score, 3)})
        return

    # 3) The bubble can expire in the milliseconds before the tap lands —
    #    verify we didn't slip into chat, and back out right away if we did.
    await asyncio.sleep(_POST_TAP_SETTLE_S)
    after = await _capture(actions, ctx.instance_id)
    if after is not None:
        chat = _chat_score(after)
        if chat >= _CHAT_THRESHOLD:
            escaped = await _tap_back(actions, ctx, after)
            ctx.result.update(
                {
                    "action": "tapped_then_chat_escape" if escaped else "tapped_chat_escape_blocked",
                    "score": round(best_score, 3),
                    "chat_score": round(chat, 3),
                }
            )
            return
    ctx.result.update({"action": "tapped", "score": round(best_score, 3)})


DSL_EXEC_HANDLERS = {
    "play_helper": _exec_play_helper,
}
