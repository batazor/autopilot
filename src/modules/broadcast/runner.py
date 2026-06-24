"""IO orchestrator for one broadcast tick (shared by every game's ``exec.py``).

A per-game ``chat/exec.py`` registers a one-line handler that calls
:func:`run_broadcast_tick` with its ``game``. The flow:

1. resolve the active player's alliance (its own state);
2. elect the broadcaster for that alliance (lowest active eligible fid) — skip if
   this account isn't it;
3. select the single due message (cooldown + cron/event trigger);
4. take a short ``SET NX EX`` claim lock (same-tick race guard);
5. navigate to ``chat.alliance``, tap the input, type the text, tap send;
6. stamp the per-alliance cooldown key + append to the send-log; return home.

Mostly a no-op: steps 1-3 are cheap Redis/SQLite reads, so the device is only
driven when a message is actually due *and* this account is the broadcaster.

The cooldown + claim locks make a duplicate post self-correcting: whoever stamps
the cooldown first wins; the rest see it and skip. All taps go through the normal
approval path (``approval_region="broadcast"``), so click-approval still gates the
device.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from layout.types import Point

from . import db, engine, keys
from .election import Candidate, elect_broadcaster

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_CLAIM_TTL_S = 120
_FALSEY = {"0", "false", "no", "off", ""}

# Fallback tap targets (percent of 720×1280) used until ``chat.alliance.input`` /
# ``chat.alliance.send`` are labeled in the game's chat ``area.yaml``. Tuned on a
# live device during verification.
_INPUT_FALLBACK_PCT = (0.43, 0.945)
_SEND_FALLBACK_PCT = (0.905, 0.945)


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _decode(value).lower() not in _FALSEY


async def _active_fids(redis: Any) -> set[str]:
    """Accounts currently active on some device (their instance ``active_player``)."""
    out: set[str] = set()
    try:
        async for key in redis.scan_iter(match="wos:instance:*:state"):
            raw = await redis.hget(key, "active_player")
            fid = _decode(raw)
            if fid:
                out.add(fid)
    except Exception:
        logger.debug("broadcast: active-fid scan failed", exc_info=True)
    return out


def _roster(game: str) -> list[Candidate]:
    """Every account on this game with its alliance + broadcast-eligibility."""
    try:
        from config.state_sqlite import list_gamers_by_power

        gamers = list_gamers_by_power(0, game=game)
    except Exception:
        logger.debug("broadcast: roster load failed game=%s", game, exc_info=True)
        return []
    out: list[Candidate] = []
    for g in gamers:
        planner = getattr(g, "planner", {}) or {}
        out.append(
            Candidate(
                fid=str(getattr(g, "id", "") or ""),
                alliance=str(getattr(getattr(g, "alliance", None), "name", "") or "").strip(),
                eligible=_truthy(planner.get("broadcast_eligible"), default=True),
            )
        )
    return out


async def _last_sent_map(redis: Any, game: str, alliance: str, ids: list[str], now: float) -> dict[str, float | None]:
    """Per-message last-sent timestamp; a present-but-unparsable key counts as just-sent."""
    out: dict[str, float | None] = {}
    for mid in ids:
        try:
            raw = await redis.get(keys.sent_key(game, alliance, mid))
        except Exception:
            raw = None
        if raw is None:
            continue
        try:
            out[mid] = float(_decode(raw))
        except (TypeError, ValueError):
            out[mid] = now  # key exists (in cooldown) but value odd → treat as fresh
    return out


def _region_point(game: str, name: str, dev_w: int, dev_h: int) -> Point | None:
    """Center of a labeled chat region as a device point, or ``None`` if unlabeled."""
    try:
        from config.paths import repo_root
        from layout.area_lookup import screen_region_by_name
        from layout.area_manifest import load_area_doc
        from layout.bbox_percent import bbox_percent_center_to_device_point

        area_doc = load_area_doc(repo_root(), game=game)
        pair = screen_region_by_name(area_doc, name)
        bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
        if not isinstance(bbox, dict):
            return None
        return bbox_percent_center_to_device_point(bbox, dev_w, dev_h)
    except Exception:
        logger.debug("broadcast: region resolve failed name=%s", name, exc_info=True)
        return None


async def _tap(actions: Any, iid: str, pt: Point) -> None:
    await asyncio.to_thread(
        actions.tap, iid, pt, approval_region="broadcast", approval_source="broadcast"
    )


async def _clear_input(actions: Any, iid: str) -> None:
    """Empty the chat field before typing (a stale draft would corrupt the post)."""
    await asyncio.to_thread(actions.press_key, iid, "KEYCODE_MOVE_END")
    for _ in range(40):
        await asyncio.to_thread(actions.press_key, iid, "KEYCODE_DEL")


async def _type_and_send(actions: Any, game: str, iid: str, text: str) -> bool:
    """Tap input → clear → type ``text`` → tap send. False if the frame is unreadable."""
    frame = await asyncio.to_thread(actions.capture_screen_bgr, iid)
    if frame is None:
        return False
    h, w = frame.shape[:2]
    input_pt = _region_point(game, "chat.alliance.input", w, h) or Point(
        int(_INPUT_FALLBACK_PCT[0] * w), int(_INPUT_FALLBACK_PCT[1] * h)
    )
    send_pt = _region_point(game, "chat.alliance.send", w, h) or Point(
        int(_SEND_FALLBACK_PCT[0] * w), int(_SEND_FALLBACK_PCT[1] * h)
    )
    await _tap(actions, iid, input_pt)
    await asyncio.sleep(0.4)
    await _clear_input(actions, iid)
    await asyncio.to_thread(actions.type_text, iid, text)
    await asyncio.sleep(0.5)
    await _tap(actions, iid, send_pt)
    await asyncio.sleep(0.8)
    return True


async def run_broadcast_tick(ctx: DslExecContext, *, game: str) -> None:
    """Post one due alliance reminder for ``game`` if this account is the broadcaster."""
    from tasks import dsl_runtime

    redis = ctx.redis_client
    player = (ctx.player_id or "").strip()
    if redis is None or not player:
        ctx.result.update({"action": "no_target"})
        return

    # Alliance from this player's own state.
    store = None
    alliance = ""
    try:
        from config.state_store import get_state_store

        store = get_state_store().get(player)
        if store is not None:
            alliance = str(store.get("alliance.name") or "").strip()
    except Exception:
        logger.debug("broadcast: alliance lookup failed player=%s", player, exc_info=True)
    if not alliance:
        ctx.result.update({"action": "no_alliance"})
        return

    # Elect one broadcaster per alliance (deterministic, lowest active eligible fid).
    elected = elect_broadcaster(_roster(game), alliance, await _active_fids(redis))
    if elected and elected != player:
        ctx.result.update({"action": "not_broadcaster", "elected": elected, "alliance": alliance})
        return

    # Select the single due message.
    messages = db.list_messages(game=game, enabled_only=True)
    if not messages:
        ctx.result.update({"action": "no_messages"})
        return
    flat = store.to_flat_dict() if store is not None else {}
    now = time.time()
    last_sent = await _last_sent_map(redis, game, alliance, [m.id for m in messages], now)
    msg = engine.select_due_message(messages, flat, now, last_sent, game)
    if msg is None:
        ctx.result.update({"action": "none_due", "alliance": alliance})
        return

    # Same-tick race guard: only the account that takes the claim posts.
    try:
        claimed = await redis.set(keys.claim_key(game, alliance, msg.id), player, nx=True, ex=_CLAIM_TTL_S)
    except Exception:
        claimed = True  # best-effort: a Redis hiccup shouldn't block the post
    if not claimed:
        ctx.result.update({"action": "claimed_by_other", "message_id": msg.id, "alliance": alliance})
        return

    # Deliver.
    actions = dsl_runtime.bot_actions()
    nav = dsl_runtime.navigator(actions, redis_client=redis)
    if not await nav.navigate_to("chat.alliance", ctx.instance_id):
        ctx.result.update({"action": "nav_failed", "message_id": msg.id, "alliance": alliance})
        return
    if not await _type_and_send(actions, game, ctx.instance_id, msg.text):
        ctx.result.update({"action": "send_failed", "message_id": msg.id, "alliance": alliance})
        return

    # Stamp cooldown so no account reposts within the window; log; return home.
    cooldown_s = max(60, engine.min_gap_seconds(msg))
    try:
        await redis.set(keys.sent_key(game, alliance, msg.id), str(now), ex=cooldown_s)
    except Exception:
        logger.debug("broadcast: cooldown stamp failed", exc_info=True)
    try:
        db.record_send(message_id=msg.id, game=game, alliance=alliance, fid=player, text=msg.text, sent_at=now)
    except Exception:
        logger.debug("broadcast: send-log write failed", exc_info=True)
    try:
        await nav.navigate_to("main_city", ctx.instance_id)  # best-effort: post already landed
    except Exception:
        logger.debug("broadcast: return-home nav failed", exc_info=True)

    ctx.result.update(
        {"action": "sent", "message_id": msg.id, "title": msg.title, "alliance": alliance}
    )
    logger.info(
        "broadcast: posted %r to alliance=%s game=%s player=%s", msg.id, alliance, game, player
    )
