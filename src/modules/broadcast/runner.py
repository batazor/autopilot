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

from . import db, engine, keys, templating
from .election import Candidate, elect_broadcaster, elect_world_broadcaster
from .models import CHANNEL_WORLD, TRIGGER_EVENT

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_CLAIM_TTL_S = 120
# Cross-message anti-flood: minimum gap between ANY two posts to one scope, so a
# batch of simultaneously-due messages can't flood the chat in one burst.
_MIN_POST_GAP_S = 120
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
                state=str(getattr(g, "state", "") or "").strip(),
            )
        )
    return out


def _scope_for(channel: str, alliance: str, state: str) -> str:
    """De-dup scope: the alliance, or ``"world:<state>"`` for (per-state) world chat."""
    if channel == CHANNEL_WORLD:
        return f"{CHANNEL_WORLD}:{state}" if state else CHANNEL_WORLD
    return alliance


async def _calendar_ctx(redis: Any, game: str, state: str) -> dict[str, list[dict]]:
    """Decoded calendar upcoming/active for ``state`` (slugged), or empty.

    Only WoS has a live calendar; everything else returns ``{}`` so pre-event /
    templating gracefully no-op.
    """
    if game != "wos" or not state or redis is None:
        return {}
    try:
        from games.wos.core.calendar.adapter import read_shared

        shared = await read_shared(redis, state)
    except Exception:
        logger.debug("broadcast: calendar read failed state=%s", state, exc_info=True)
        return {}
    upcoming = [
        {
            "slug": templating.slug(str(ev.get("name") or "")),
            "name": str(ev.get("name") or ""),
            "in_hours": ev.get("in_hours"),
            "starts": ev.get("starts"),
        }
        for ev in (shared.get("upcoming") or [])
    ]
    active = [
        {
            "slug": templating.slug(str(ev.get("name") or "")),
            "name": str(ev.get("name") or ""),
            "ends": ev.get("ends"),
        }
        for ev in (shared.get("active") or [])
    ]
    return {"upcoming": upcoming, "active": active}


def _server_minutes(now: float) -> int | None:
    """UTC+8 (WoS server) minute-of-day for quiet-hours gating, or ``None``."""
    try:
        from datetime import UTC, datetime

        from games.wos.core.arena.reward_window import minute_of_day

        return minute_of_day(datetime.fromtimestamp(now, tz=UTC))
    except Exception:
        return None


def _template_context(
    msg: Any, calendar_ctx: dict[str, list[dict]], alliance: str, state: str
) -> dict[str, Any]:
    """Substitution values for {event}/{in_hours}/{starts}/{ends}/{alliance}/{state}."""
    out: dict[str, Any] = {
        "alliance": alliance,
        "state": state,
        "event": "",
        "in_hours": "",
        "starts": "",
        "ends": "",
    }
    if msg.trigger_kind != TRIGGER_EVENT:
        return out
    ev = engine.upcoming_match(msg, calendar_ctx) if msg.is_pre_event() else None
    if ev is None:  # live event → pull end time from the active list
        target = engine.event_slug_from_cond(msg.cond)
        for a in calendar_ctx.get("active") or []:
            if a.get("slug") == target:
                ev = a
                break
    if ev:
        out["event"] = ev.get("name") or ""
        if ev.get("in_hours") is not None:
            out["in_hours"] = ev.get("in_hours")
        out["starts"] = ev.get("starts") or ""
        out["ends"] = ev.get("ends") or ""
    return out


async def _within_flood_gap(redis: Any, game: str, scope: str, now: float) -> bool:
    """True if a post to ``scope`` happened within the anti-flood window."""
    try:
        raw = await redis.get(keys.last_post_key(game, scope))
    except Exception:
        return False
    last = _decode(raw)
    if not last:
        return False
    try:
        return (now - float(last)) < _MIN_POST_GAP_S
    except (TypeError, ValueError):
        return False


async def _last_sent_map(
    redis: Any, game: str, scoped_ids: list[tuple[str, str]], now: float
) -> dict[str, float | None]:
    """Per-message last-sent timestamp; a present-but-unparsable key counts as just-sent.

    ``scoped_ids`` is ``[(message_id, scope), ...]`` so each message is looked up
    under its own de-dup scope (alliance vs world).
    """
    out: dict[str, float | None] = {}
    for mid, scope in scoped_ids:
        try:
            raw = await redis.get(keys.sent_key(game, scope, mid))
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


def _resolve_identity(player: str) -> tuple[Any, str, str]:
    """``(store, alliance, state)`` for ``player`` (alliance/state may be empty)."""
    try:
        from config.state_store import get_state_store

        store = get_state_store().get(player)
    except Exception:
        logger.debug("broadcast: identity lookup failed player=%s", player, exc_info=True)
        return (None, "", "")
    if store is None:
        return (None, "", "")
    alliance = str(store.get("alliance.name") or "").strip()
    state = str(store.get("state") or "").strip()
    return (store, alliance, state)


def _targets_alliance(msg: Any, alliance: str) -> bool:
    """Alliance-channel targeting: a blank target means every alliance."""
    if msg.channel == CHANNEL_WORLD:
        return True
    tgt = str(getattr(msg, "target_alliance", "") or "").strip()
    return not tgt or tgt == alliance


async def _deliver_text(
    redis: Any, game: str, instance_id: str, *, channel: str, text: str
) -> str:
    """Navigate to the channel's tab and post ``text``. Returns a result action."""
    from tasks import dsl_runtime

    node = "chat.world" if channel == CHANNEL_WORLD else "chat.alliance"
    actions = dsl_runtime.bot_actions()
    nav = dsl_runtime.navigator(actions, redis_client=redis)
    if not await nav.navigate_to(node, instance_id):
        return "nav_failed"
    if not await _type_and_send(actions, game, instance_id, text):
        return "send_failed"
    try:
        await nav.navigate_to("main_city", instance_id)  # best-effort: post already landed
    except Exception:
        logger.debug("broadcast: return-home nav failed", exc_info=True)
    return "sent"


async def run_broadcast_tick(ctx: DslExecContext, *, game: str) -> None:
    """Post one due reminder for ``game`` (alliance or world chat) if this account
    is the elected broadcaster for that message's channel."""
    redis = ctx.redis_client
    player = (ctx.player_id or "").strip()
    if redis is None or not player:
        ctx.result.update({"action": "no_target"})
        return

    store, alliance, state = _resolve_identity(player)

    messages = db.list_messages(game=game, enabled_only=True)
    if not messages:
        ctx.result.update({"action": "no_messages"})
        return
    # Alliance-channel messages need an alliance and must target it; world don't.
    usable = [
        m
        for m in messages
        if (m.channel == CHANNEL_WORLD or alliance) and _targets_alliance(m, alliance)
    ]
    if not usable:
        ctx.result.update({"action": "no_alliance"})
        return

    # Select the single due message (across both channels), honouring pre-event
    # windows (calendar) and quiet hours (server time).
    flat = store.to_flat_dict() if store is not None else {}
    now = time.time()
    calendar_ctx = await _calendar_ctx(redis, game, state)
    server_minutes = _server_minutes(now)
    scoped = [(m.id, _scope_for(m.channel, alliance, state)) for m in usable]
    last_sent = await _last_sent_map(redis, game, scoped, now)
    msg = engine.select_due_message(
        usable, flat, now, last_sent, game,
        calendar_ctx=calendar_ctx, server_minutes=server_minutes,
    )
    if msg is None:
        ctx.result.update({"action": "none_due", "alliance": alliance})
        return
    scope = _scope_for(msg.channel, alliance, state)

    # Elect one broadcaster: per-alliance for alliance chat, per-state for world.
    active = await _active_fids(redis)
    roster = _roster(game)
    if msg.channel == CHANNEL_WORLD:
        elected = elect_world_broadcaster(roster, state, active)
    else:
        elected = elect_broadcaster(roster, alliance, active)
    if elected and elected != player:
        ctx.result.update(
            {"action": "not_broadcaster", "elected": elected, "channel": msg.channel}
        )
        return

    # Same-tick race guard: only the account that takes the claim posts.
    try:
        claimed = await redis.set(keys.claim_key(game, scope, msg.id), player, nx=True, ex=_CLAIM_TTL_S)
    except Exception:
        claimed = True  # best-effort: a Redis hiccup shouldn't block the post
    if not claimed:
        ctx.result.update({"action": "claimed_by_other", "message_id": msg.id, "scope": scope})
        return

    # Cross-message anti-flood: don't post if this scope posted very recently.
    if await _within_flood_gap(redis, game, scope, now):
        ctx.result.update({"action": "throttled", "message_id": msg.id, "scope": scope})
        return

    text = templating.render(msg.text, _template_context(msg, calendar_ctx, alliance, state))
    action = await _deliver_text(redis, game, ctx.instance_id, channel=msg.channel, text=text)
    if action != "sent":
        ctx.result.update({"action": action, "message_id": msg.id, "scope": scope})
        return

    # Stamp cooldown + anti-flood marker; log the post.
    cooldown_s = max(60, engine.min_gap_seconds(msg))
    try:
        await redis.set(keys.sent_key(game, scope, msg.id), str(now), ex=cooldown_s)
        await redis.set(keys.last_post_key(game, scope), str(now), ex=max(_MIN_POST_GAP_S, 60))
    except Exception:
        logger.debug("broadcast: cooldown stamp failed", exc_info=True)
    try:
        db.record_send(message_id=msg.id, game=game, alliance=scope, fid=player, text=text, sent_at=now)
    except Exception:
        logger.debug("broadcast: send-log write failed", exc_info=True)

    ctx.result.update(
        {"action": "sent", "message_id": msg.id, "title": msg.title, "channel": msg.channel, "scope": scope}
    )
    logger.info(
        "broadcast: posted %r to %s (scope=%s) game=%s player=%s",
        msg.id, msg.channel, scope, game, player,
    )


async def send_one(ctx: DslExecContext, *, game: str, message_id: str) -> None:
    """Post ONE specific message right now (operator "Send now" test).

    Bypasses election / cooldown / claim / anti-flood — it's a manual one-off — but
    still renders templates and goes through the normal approval-gated taps.
    """
    redis = ctx.redis_client
    player = (ctx.player_id or "").strip()
    mid = str(message_id or "").strip()
    if not mid:
        ctx.result.update({"action": "no_message_id"})
        return
    msg = db.get_message(mid)
    if msg is None:
        ctx.result.update({"action": "unknown_message", "message_id": mid})
        return

    _store, alliance, state = _resolve_identity(player)
    calendar_ctx = await _calendar_ctx(redis, game, state)
    text = templating.render(msg.text, _template_context(msg, calendar_ctx, alliance, state))

    action = await _deliver_text(redis, game, ctx.instance_id, channel=msg.channel, text=text)
    if action == "sent" and player:
        try:
            db.record_send(
                message_id=msg.id, game=game,
                alliance=_scope_for(msg.channel, alliance, state),
                fid=player, text=text, sent_at=time.time(),
            )
        except Exception:
            logger.debug("broadcast: send-now log write failed", exc_info=True)
    ctx.result.update({"action": action, "message_id": msg.id, "title": msg.title, "manual": True})
    logger.info("broadcast: send-now %r → %s game=%s", msg.id, action, game)
