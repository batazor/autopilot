"""``exec: fetch_player`` — sync player identity/state from the Century API."""
from __future__ import annotations

import contextlib
import logging
import re
import time

from century.api import CenturyAPIError, CenturyClient, PlayerData
from config.devices import clear_last_active_player, upsert_device_gamer
from config.paths import repo_root
from config.state_store import get_state_store
from dashboard.notifications import push_ui_notification
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
)

logger = logging.getLogger(__name__)

_DEVICES_PATH = repo_root() / "db" / "devices.yaml"
_FETCH_PLAYER_TTL_SECONDS = 15 * 60
_FETCH_PLAYER_FAILURE_TTL_SECONDS = 15 * 60
_CENTURY_ROLE_NOT_EXIST_ERR_CODE = "40001"
_CENTURY_ERR_CODE_RE = re.compile(r"\berr_code=(?P<err_code>\d+)\b", re.IGNORECASE)


def _century_error_code(exc: CenturyAPIError) -> str:
    raw = getattr(exc, "err_code", None)
    if raw is not None:
        return str(raw).strip()
    m = _CENTURY_ERR_CODE_RE.search(str(exc))
    return m.group("err_code") if m else ""


def _century_error_message(exc: CenturyAPIError) -> str:
    raw = getattr(exc, "api_msg", None)
    return str(raw).strip() if raw is not None else str(exc).strip()


async def _clear_active_player_if_matches(
    ctx: DslExecContext,
    *,
    player_id: str,
    reason: str,
) -> None:
    if ctx.redis_client is None or not player_id:
        return
    instance_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        raw_active = await ctx.redis_client.hget(instance_key, "active_player")
        active = _decode_redis_raw(raw_active)
        with contextlib.suppress(Exception):
            clear_last_active_player(ctx.instance_id, player_id)
        if active != player_id:
            return
        now = time.time()
        await ctx.redis_client.hdel(instance_key, "active_player", "active_player_at")
        await ctx.redis_client.hset(
            instance_key,
            mapping={
                "invalid_player_id": player_id,
                "invalid_player_id_at": str(now),
                "invalid_player_id_reason": reason,
            },
        )
        logger.info(
            "dsl exec fetch_player: cleared invalid active_player=%s on %s (%s)",
            player_id,
            ctx.instance_id,
            reason,
        )
    except Exception:
        logger.debug(
            "dsl exec fetch_player: failed to clear invalid active_player",
            exc_info=True,
        )


async def _recent_fetch_failure(
    ctx: DslExecContext,
    *,
    state_key: str,
    fid: int,
) -> bool:
    if ctx.redis_client is None:
        return False
    try:
        raw_ts = await ctx.redis_client.hget(state_key, "century_player_sync_failed_at")
        ts_s = _decode_redis_raw(raw_ts)
        ts = float(ts_s) if ts_s else 0.0
    except Exception:
        ts = 0.0
    if not ts:
        return False
    age = time.time() - ts
    if age >= _FETCH_PLAYER_FAILURE_TTL_SECONDS:
        return False
    try:
        err_code = _decode_redis_raw(
            await ctx.redis_client.hget(state_key, "century_player_sync_err_code")
        )
        error = _decode_redis_raw(
            await ctx.redis_client.hget(state_key, "century_player_sync_error")
        )
    except Exception:
        err_code = ""
        error = ""
    if err_code == _CENTURY_ROLE_NOT_EXIST_ERR_CODE:
        await _clear_active_player_if_matches(
            ctx,
            player_id=ctx.player_id.strip(),
            reason=f"century_err_code_{err_code}",
        )
    logger.info(
        "dsl exec fetch_player: skip by failure TTL fid=%s age=%.1fs err_code=%s error=%r",
        fid,
        age,
        err_code or "-",
        error,
    )
    return True


async def _remember_fetch_failure(
    ctx: DslExecContext,
    *,
    state_key: str,
    fid: int,
    exc: CenturyAPIError,
) -> str:
    err_code = _century_error_code(exc)
    error = _century_error_message(exc) or str(exc)
    if ctx.redis_client is None:
        return err_code
    try:
        await ctx.redis_client.hset(
            state_key,
            mapping={
                "century_player_sync_failed_at": str(time.time()),
                "century_player_sync_error": error,
                "century_player_sync_err_code": err_code,
            },
        )
    except Exception:
        logger.debug(
            "dsl exec fetch_player: failed to persist API failure fid=%s",
            fid,
            exc_info=True,
        )
    return err_code


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
    if await _recent_fetch_failure(ctx, state_key=state_key, fid=fid):
        return

    try:
        data: PlayerData = await CenturyClient().fetch_player(fid)
    except CenturyAPIError as exc:
        err_code = await _remember_fetch_failure(
            ctx,
            state_key=state_key,
            fid=fid,
            exc=exc,
        )
        if err_code == _CENTURY_ROLE_NOT_EXIST_ERR_CODE:
            await _clear_active_player_if_matches(
                ctx,
                player_id=ctx.player_id.strip(),
                reason=f"century_err_code_{err_code}",
            )
        logger.warning(
            "dsl exec fetch_player: API error fid=%s err_code=%s: %s",
            fid,
            err_code or "-",
            exc,
        )
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
        with contextlib.suppress(Exception):
            await ctx.redis_client.hdel(
                state_key,
                "century_player_sync_failed_at",
                "century_player_sync_error",
                "century_player_sync_err_code",
            )
    except Exception:
        logger.exception("dsl exec fetch_player: redis hset failed key=%s", state_key)
        return

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=ctx.player_id,
        reason="fetch_player",
    )

    # Persist player snapshot to the SQLite state store.
    try:
        store = get_state_store().get_or_create(ctx.player_id, nickname=data.nickname)
        store.update_from_flat(
            {
                "nickname": data.nickname,
                "kid": data.kid,
                "avatar": data.avatar_image or "",
                "buildings.furnace.level": data.stove_level,
                "buildings.furnace.power": data.stove_lv_content,
                "buildings.levels.furnace": int(data.stove_level),
                "century_player_sync_at": float(time.time()),
            }
        )
    except Exception:
        logger.exception("dsl exec fetch_player: state persist failed fid=%s", fid)

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
