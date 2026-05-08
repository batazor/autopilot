"""Named handlers for DSL ``exec:`` steps (see :class:`tasks.dsl_scenario.DslScenarioTask`)."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from actions.tap import BotActions
from century.api import CenturyAPIError, CenturyClient, PlayerData
from config.devices import upsert_device_gamer
from config.state_store import get_state_store
from gift.redeemer import run_gift_code_redeemer
from gift.scraper import poll_once
from navigation.navigator import Navigator
from ui.notifications import push_ui_notification

_CODES_PATH = Path("db/giftCodes.yaml")
_DEVICES_PATH = Path("db/devices.yaml")

logger = logging.getLogger(__name__)

DslExecHandler = Callable[["DslExecContext"], Awaitable[None]]

_FETCH_PLAYER_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class DslExecContext:
    redis_client: Any | None
    """Async Redis client (same as ``DslScenarioTask.redis_client``)."""

    player_id: str
    """Queue / config player id (Redis hash ``wos:player:<player_id>:state``)."""

    instance_id: str
    """ADB instance id (device)."""


def _decode_redis_raw(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw).strip()


async def _exec_fetch_player(ctx: DslExecContext) -> None:
    """POST Century ``/api/player`` using OCR'd ``player_id`` and persist profile fields."""
    if ctx.redis_client is None:
        logger.warning("dsl exec fetch_player: no redis client — skipping")
        return
    if not ctx.player_id.strip():
        logger.warning("dsl exec fetch_player: empty task player_id — skipping")
        return

    state_key = f"wos:player:{ctx.player_id}:state"
    raw_fid = await ctx.redis_client.hget(state_key, "player_id")
    fid_s = _decode_redis_raw(raw_fid)
    if not fid_s:
        logger.warning(
            "dsl exec fetch_player: missing player_id field on %s — run ocr first",
            state_key,
        )
        return
    try:
        fid = int(fid_s)
    except ValueError:
        logger.warning("dsl exec fetch_player: invalid fid %r on %s", fid_s, state_key)
        return

    # TTL guard: skip Century API if we synced recently (UI button is never disabled,
    # but we avoid excessive calls from repeated runs / cron).
    try:
        raw_ts = await ctx.redis_client.hget(state_key, "century_player_sync_at")
        ts_s = _decode_redis_raw(raw_ts)
        ts = float(ts_s) if ts_s else 0.0
    except Exception:
        ts = 0.0
    if ts and (time.time() - ts) < _FETCH_PLAYER_TTL_SECONDS:
        logger.info(
            "dsl exec fetch_player: skip by TTL fid=%s age=%.1fs",
            fid,
            time.time() - ts,
        )
        return

    try:
        data: PlayerData = await CenturyClient().fetch_player(fid)
    except CenturyAPIError as exc:
        logger.warning("dsl exec fetch_player: API error fid=%s: %s", fid, exc)
        return
    except Exception:
        logger.exception("dsl exec fetch_player: unexpected error fid=%s", fid)
        return

    mapping: dict[str, str] = {
        "nickname": data.nickname,
        "stove_level": str(data.stove_level),
        "kid": str(data.kid),
        "stove_lv_content": str(data.stove_lv_content),
        "avatar_image": data.avatar_image or "",
        "century_player_sync_at": str(time.time()),
    }
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec fetch_player: redis hset failed key=%s", state_key)
        return

    # Persist to db/state.yaml
    try:
        store = get_state_store().get_or_create(ctx.player_id, nickname=data.nickname)
        store.update_from_flat(
            {
                "nickname": data.nickname,
                "kid": data.kid,
                "avatar": data.avatar_image or "",
                "buildings.furnace.level": data.stove_level,
                "buildings.furnace.power": data.stove_lv_content,
                "century_player_sync_at": float(time.time()),
            }
        )
    except Exception:
        logger.exception("dsl exec fetch_player: state.yaml persist failed fid=%s", fid)

    # Persist to db/devices.yaml under current instance_id
    try:
        upsert_device_gamer(
            path=_DEVICES_PATH,
            device_name=ctx.instance_id,
            player_id=ctx.player_id,
            nickname=data.nickname,
        )
    except Exception:
        logger.exception("dsl exec fetch_player: devices.yaml upsert failed fid=%s", fid)

    logger.info(
        "dsl exec fetch_player: synced fid=%s nickname=%r stove_level=%s",
        fid,
        data.nickname,
        data.stove_level,
    )

    # UI toast — fires once per browser tab via the seen-set in click_approvals.
    nick = (data.nickname or "?").strip() or "?"
    msg = f"Player synced: {nick} · stove {data.stove_level} · fid {fid}"
    await push_ui_notification(
        ctx.redis_client,
        ctx.instance_id,
        kind="exec.fetch_player",
        message=msg,
        level="success",
        payload={
            "player_id": ctx.player_id,
            "fid": fid,
            "nickname": data.nickname,
            "stove_level": data.stove_level,
            "kid": data.kid,
        },
    )


async def _exec_detect_screen(ctx: DslExecContext) -> None:
    """Detect current page and persist ``wos:instance:<id>:state.current_screen``."""
    actions = BotActions()
    navigator = Navigator(
        actions.capture_screen_bgr,
        actions.tap,
        redis_client=ctx.redis_client,
    )
    detected = await navigator.detect_current_screen(ctx.instance_id)
    logger.info(
        "dsl exec detect_screen: instance=%s detected=%s",
        ctx.instance_id,
        detected or "(unknown)",
    )


async def _exec_gift_code_scrape(ctx: DslExecContext) -> None:
    """Scrape wosrewards.com for new gift codes and append them to giftCodes.yaml."""
    try:
        new = await poll_once(_CODES_PATH)
    except Exception:
        logger.exception("dsl exec gift_code_scrape: scraper failed")
        return
    if new:
        logger.info("dsl exec gift_code_scrape: found %d new code(s): %s", len(new), ", ".join(new))
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_scrape",
            message=f"New gift codes found: {', '.join(new)}",
            level="info",
            payload={"codes": new},
        )
    else:
        logger.info("dsl exec gift_code_scrape: no new codes")


async def _exec_gift_code_redeem(ctx: DslExecContext) -> None:
    """Redeem all pending gift codes for all players listed in devices.yaml."""
    if not _CODES_PATH.exists():
        logger.warning("dsl exec gift_code_redeem: %s not found — skipping", _CODES_PATH)
        return
    if not _DEVICES_PATH.exists():
        logger.warning("dsl exec gift_code_redeem: %s not found — skipping", _DEVICES_PATH)
        return
    try:
        await run_gift_code_redeemer(_CODES_PATH, _DEVICES_PATH)
    except Exception:
        logger.exception("dsl exec gift_code_redeem: redeemer failed")
        return
    logger.info("dsl exec gift_code_redeem: done")


DSL_EXEC_REGISTRY: dict[str, DslExecHandler] = {
    "detect_screen": _exec_detect_screen,
    "fetch_player": _exec_fetch_player,
    "gift_code_scrape": _exec_gift_code_scrape,
    "gift_code_redeem": _exec_gift_code_redeem,
}
