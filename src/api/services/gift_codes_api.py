"""Gift codes dashboard data."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

from century.gift_codes import discord_source
from century.gift_codes import kingshot as kingshot_gift_codes
from century.gift_codes import kingshot_beta as kingshot_beta_gift_codes
from century.gift_codes import wos as wos_gift_codes
from century.gift_codes import wos_beta as wos_beta_gift_codes
from century.gift_codes.models import RedeemStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

from config.devices import load_devices
from config.giftcodes_db import (
    delete_gift_code_setting,
    list_codes,
    set_gift_code_setting,
)
from config.loader import load_settings
from config.paths import repo_root
from config.redis_metrics import instrument_redis_client
from config.state_sqlite import state_db_path

_REPO = repo_root()
logger = logging.getLogger(__name__)

_REDEEMED = frozenset({RedeemStatus.SUCCESS.value, RedeemStatus.ALREADY_RECEIVED.value})
_GIFT_CODE_POLL_INTERVAL_SECONDS = 6 * 60 * 60
_GIFT_CODE_LOCK_TTL_SECONDS = 2 * 60 * 60
_STARTUP_SCRAPE_ENV = "WOS_GIFT_CODES_STARTUP_SCRAPE"
_DISCORD_WOS_BETA_CHANNEL_ENV = "WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID"
_DISCORD_KINGSHOT_BETA_CHANNEL_ENV = "KINGSHOT_BETA_GIFT_CODES_DISCORD_CHANNEL_ID"
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class _GiftCodeGame:
    game: str
    redeem_lock_key: str
    poll_once: Callable[[], Awaitable[list[str]]]
    run_redeemer: Callable[..., Awaitable[Any]]
    run_redeemer_for_player: Callable[..., Awaitable[Any]] | None = None
    redeem_supported: bool = True
    apply_mode: str = "api_all_accounts"


_GIFT_CODE_GAMES: dict[str, _GiftCodeGame] = {
    "wos": _GiftCodeGame(
        game="wos",
        redeem_lock_key="wos:gift_code_redeem:lock",
        poll_once=wos_gift_codes.poll_once,
        run_redeemer=wos_gift_codes.run_gift_code_redeemer,
        run_redeemer_for_player=wos_gift_codes.run_gift_code_redeemer_for_player,
    ),
    "kingshot": _GiftCodeGame(
        game="kingshot",
        redeem_lock_key="wos:gift_code_redeem:lock:kingshot",
        poll_once=kingshot_gift_codes.poll_once,
        run_redeemer=kingshot_gift_codes.run_gift_code_redeemer,
        run_redeemer_for_player=getattr(
            kingshot_gift_codes, "run_gift_code_redeemer_for_player", None
        ),
    ),
    "wos_beta": _GiftCodeGame(
        game="wos_beta",
        redeem_lock_key="wos:gift_code_redeem:lock:wos_beta",
        poll_once=wos_beta_gift_codes.poll_once,
        run_redeemer=wos_beta_gift_codes.run_gift_code_redeemer,
        redeem_supported=False,
        apply_mode="in_game_player",
    ),
    "kingshot_beta": _GiftCodeGame(
        game="kingshot_beta",
        redeem_lock_key="wos:gift_code_redeem:lock:kingshot_beta",
        poll_once=kingshot_beta_gift_codes.poll_once,
        run_redeemer=kingshot_beta_gift_codes.run_gift_code_redeemer,
        redeem_supported=False,
        apply_mode="in_game_player",
    ),
}


def _gift_code_game(game: str) -> _GiftCodeGame:
    game_id = (game or "wos").strip().lower()
    try:
        return _GIFT_CODE_GAMES[game_id]
    except KeyError as exc:
        known = ", ".join(sorted(_GIFT_CODE_GAMES))
        msg = f"unknown gift-code game {game_id!r}; expected one of: {known}"
        raise ValueError(msg) from exc


def _startup_scrape_enabled() -> bool:
    raw = os.environ.get(_STARTUP_SCRAPE_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _poll_cadence_key(game: str) -> str:
    # Shared with SchedulerRunner._run_gift_codes_polling so API startup and the
    # bot worker cannot independently run the same global cycle.
    return f"wos:scheduler:gift_codes_poll:{game}"


@asynccontextmanager
async def _api_gift_code_redis() -> AsyncIterator[Any]:
    settings = load_settings()
    client = aioredis.from_url(
        settings.redis.url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=5.0,
    )
    instrument_redis_client(client, component="api_gift_codes")
    try:
        yield client
    finally:
        with suppress(Exception):
            await client.aclose()


async def _release_redeem_lock(
    redis_client: Any,
    key: str,
    token: str,
) -> None:
    try:
        await redis_client.eval(_RELEASE_LOCK_LUA, 1, key, token)
    except Exception:
        logger.debug("gift-code redeem lock release failed (key=%s)", key, exc_info=True)


def _status_token(cell: str) -> str:
    raw = str(cell or "").strip()
    if not raw or raw == "—":
        return ""
    return raw.split(" ", 1)[0].strip()


def _display_state_db_path() -> str:
    path = state_db_path()
    try:
        return str(path.relative_to(_REPO))
    except ValueError:
        return str(path)


def _build_row(code: Any, player_ids: list[str], registry: Any) -> dict[str, Any]:
    api_err = str(code.last_api_err_code) if code.last_api_err_code is not None else "—"
    row: dict[str, Any] = {
        "code": code.name,
        "expires": code.expires.isoformat() if code.expires else "—",
        "slot_expired": code.is_effectively_expired(),
        "needs_run": bool(
            not code.is_effectively_expired()
            and any(code.needs_redemption(pid) for pid in player_ids)
        ),
        "api_err": api_err,
        "api_msg": code.last_api_msg or "—",
        "players": {},
    }
    for pid in player_ids:
        status = code.user_for.get(pid, RedeemStatus.PENDING)
        gamer = registry.get_gamer(pid)
        nick = (gamer.nickname or "").strip() if gamer else ""
        row["players"][pid] = {
            "status": status.value,
            "nickname": nick,
            "label": f"{status.value} · {nick}" if nick else status.value,
        }
    return row


def build_gift_codes_view(*, query: str = "", game: str = "wos") -> dict[str, Any]:
    spec = _gift_code_game(game)
    codes = list_codes(game=game)
    registry = load_devices()
    player_ids = list(dict.fromkeys(registry.all_player_ids(game=game)))
    codes_path = _display_state_db_path()
    for c in codes:
        for pid in c.user_for:
            if pid not in player_ids:
                player_ids.append(pid)

    q = query.strip().lower()
    active: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    pending_slots = 0
    needs_run_count = 0
    redeemed_slots = 0

    for code in codes:
        row = _build_row(code, player_ids, registry)
        hay = " ".join(
            [
                str(row["code"]),
                str(row["api_msg"]),
                *(
                    f"{p['status']} {p.get('nickname', '')}"
                    for p in row["players"].values()
                ),
            ]
        ).lower()
        if q and q not in hay:
            continue
        for p in row["players"].values():
            if _status_token(p["status"]) == RedeemStatus.PENDING.value:
                pending_slots += 1
            if _status_token(p["status"]) in _REDEEMED:
                redeemed_slots += 1
        if row["needs_run"]:
            needs_run_count += 1
        if row["slot_expired"]:
            expired.append(row)
        else:
            active.append(row)

    return {
        "game": game,
        "redeem_supported": spec.redeem_supported,
        "apply_mode": spec.apply_mode,
        "codes_db": codes_path,
        "codes_path": codes_path,
        "devices_path": codes_path,
        "parse_error": None,
        "missing_codes_file": False,
        "player_ids": player_ids,
        "active": active,
        "expired": expired,
        "metrics": {
            "total": len(active) + len(expired),
            "active": len(active),
            "expired": len(expired),
            "needs_run": needs_run_count,
            "pending_slots": pending_slots,
            "redeemed_slots": redeemed_slots,
        },
    }


async def poll_status(game: str) -> dict[str, Any]:
    """Seconds until the next global scrape cycle for ``game``.

    The scheduler claims a per-game cadence key with a 6h TTL on each cycle
    (see ``SchedulerRunner._run_gift_codes_polling``); the remaining TTL is the
    countdown to the next poll. A missing key means the window elapsed and the
    next scheduler tick (~30s) will poll, so we report 0 ("due now"). Redis
    being unavailable yields ``None`` (unknown).
    """
    spec = _gift_code_game(game)
    next_poll_seconds: int | None
    try:
        async with _api_gift_code_redis() as redis_client:
            ttl = await redis_client.ttl(_poll_cadence_key(spec.game))
    except Exception:
        logger.debug("gift-code poll-status: redis unavailable", exc_info=True)
        ttl = None
    if ttl is None:
        next_poll_seconds = None
    elif ttl < 0:
        # -2 = key missing (window elapsed; next tick polls), -1 = no expiry set.
        next_poll_seconds = 0
    else:
        next_poll_seconds = int(ttl)
    return {
        "game": spec.game,
        "interval_seconds": _GIFT_CODE_POLL_INTERVAL_SECONDS,
        "next_poll_seconds": next_poll_seconds,
    }


def build_discord_config_view() -> dict[str, Any]:
    token_source = discord_source.discord_token_source()
    return {
        "token_configured": token_source != "none",
        "token_source": token_source,
        "wos_beta_channel_id": discord_source.discord_channel_id(
            _DISCORD_WOS_BETA_CHANNEL_ENV
        ),
        "wos_beta_channel_source": discord_source.discord_channel_source(
            _DISCORD_WOS_BETA_CHANNEL_ENV
        ),
        "kingshot_beta_channel_id": discord_source.discord_channel_id(
            _DISCORD_KINGSHOT_BETA_CHANNEL_ENV
        ),
        "kingshot_beta_channel_source": discord_source.discord_channel_source(
            _DISCORD_KINGSHOT_BETA_CHANNEL_ENV
        ),
    }


def update_discord_config(
    *,
    bot_token: str | None = None,
    clear_token: bool = False,
) -> dict[str, Any]:
    if clear_token:
        delete_gift_code_setting(discord_source.DISCORD_TOKEN_SETTING_KEY)
    elif bot_token is not None and bot_token.strip():
        set_gift_code_setting(discord_source.DISCORD_TOKEN_SETTING_KEY, bot_token.strip())

    return build_discord_config_view()


async def scrape_gift_codes() -> dict[str, Any]:
    new = await scrape_gift_codes_for_game("wos")
    return {"ok": True, "game": "wos", "new_codes": new, "count": len(new)}


async def scrape_gift_codes_for_game(game: str = "wos") -> list[str]:
    spec = _gift_code_game(game)
    return await spec.poll_once()


async def redeem_gift_codes(game: str = "wos") -> dict[str, Any]:
    spec = _gift_code_game(game)
    if not spec.redeem_supported:
        return {
            "ok": False,
            "game": spec.game,
            "redeem_supported": False,
            "reason": "beta_codes_apply_in_game_for_current_player",
        }
    async with _api_gift_code_redis() as client:
        token = f"api:manual:{spec.game}:{int(time.time())}"
        acquired = await client.set(
            spec.redeem_lock_key,
            token,
            nx=True,
            ex=_GIFT_CODE_LOCK_TTL_SECONDS,
        )
        if not acquired:
            return {"ok": False, "game": spec.game, "already_running": True}
        try:
            summary = await spec.run_redeemer()
        finally:
            await _release_redeem_lock(client, spec.redeem_lock_key, token)
    counts = summary.counts_by_status() if hasattr(summary, "counts_by_status") else {}
    total = len(getattr(summary, "results", []) or [])
    return {"ok": True, "game": spec.game, "total": total, "counts": counts}


async def redeem_gift_codes_for_player(
    player_id: int,
    *,
    game: str = "wos",
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> Any:
    spec = _gift_code_game(game)
    if spec.run_redeemer_for_player is None:
        msg = f"single-account gift-code redeem is not implemented for {spec.game}"
        raise ValueError(msg)
    return await spec.run_redeemer_for_player(player_id, progress_cb=progress_cb)


async def startup_scrape_gift_codes_once(
    redis_client: Any,
    *,
    ttl_s: int = _GIFT_CODE_POLL_INTERVAL_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Run the shared gift-code poll cycle once per TTL for API startup.

    Uses the same cadence and redeem-lock keys as ``SchedulerRunner``. If the API
    refreshes gift codes on ``uv run play`` startup, the bot scheduler will see
    the TTL and skip its duplicate cycle.
    """
    out: dict[str, dict[str, Any]] = {}
    for game_id, spec in _GIFT_CODE_GAMES.items():
        key = _poll_cadence_key(game_id)
        acquired = await redis_client.set(key, "1", nx=True, ex=ttl_s)
        if not acquired:
            out[game_id] = {"status": "skipped", "reason": "ttl"}
            continue
        if not spec.redeem_supported:
            try:
                new = await spec.poll_once()
            except Exception as exc:
                logger.exception("gift-code startup scrape failed for %s", game_id)
                out[game_id] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc!s}",
                }
                continue
            out[game_id] = {
                "status": "done",
                "new_codes": new,
                "count": len(new),
                "redeem_skipped": True,
                "reason": "beta_codes_apply_in_game_for_current_player",
            }
            logger.info(
                "gift-code startup cycle[%s]: %d new code(s), redeem skipped",
                game_id,
                len(new),
            )
            continue
        token = f"api:startup:{game_id}:{int(time.time())}"
        redeem_acquired = await redis_client.set(
            spec.redeem_lock_key,
            token,
            nx=True,
            ex=_GIFT_CODE_LOCK_TTL_SECONDS,
        )
        if not redeem_acquired:
            out[game_id] = {"status": "skipped", "reason": "redeem_lock"}
            continue
        try:
            new = await spec.poll_once()
            summary = await spec.run_redeemer()
        except Exception as exc:
            logger.exception("gift-code startup cycle failed for %s", game_id)
            out[game_id] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc!s}",
            }
            continue
        finally:
            await _release_redeem_lock(redis_client, spec.redeem_lock_key, token)
        counts = summary.counts_by_status() if hasattr(summary, "counts_by_status") else {}
        total = len(getattr(summary, "results", []) or [])
        out[game_id] = {
            "status": "done",
            "new_codes": new,
            "count": len(new),
            "redeem_total": total,
            "redeem_counts": counts,
        }
        logger.info(
            "gift-code startup cycle[%s]: %d new code(s), redeem total=%d",
            game_id,
            len(new),
            total,
        )
    return out


async def run_startup_gift_code_scrape() -> dict[str, dict[str, Any]] | None:
    """Best-effort API startup hook for code-list refresh.

    Redis is used only as the cross-process TTL gate. If Redis is unavailable,
    startup continues normally and the dashboard can still use manual buttons.
    """
    if not _startup_scrape_enabled():
        logger.debug("%s=0; gift-code startup scrape disabled", _STARTUP_SCRAPE_ENV)
        return None

    try:
        async with _api_gift_code_redis() as client:
            return await startup_scrape_gift_codes_once(client)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("gift-code startup scrape skipped", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# External accounts service layer
#
# Redeem codes against accounts the bot doesn't own (alliance members, partner
# farms, etc.). The redeemer (games/*/gift_codes/redeemer.py) folds these rows
# into the redeem pass.
# ---------------------------------------------------------------------------


def _ext_to_dict(ext: Any) -> dict[str, Any]:
    return {
        "game": ext.game,
        "player_id": ext.player_id,
        "nickname": ext.nickname,
        "label": ext.label,
        "enabled": ext.enabled,
        "added_at": ext.added_at,
        "last_seen_at": ext.last_seen_at,
    }


def list_external_accounts(*, game: str = "wos") -> dict[str, Any]:
    """Read all external accounts for ``game``."""
    from config.giftcodes_db import list_external_gamers

    rows = list_external_gamers(game=game)
    return {
        "game": game,
        "accounts": [_ext_to_dict(r) for r in rows],
        "count": len(rows),
    }


async def upsert_external_account(
    *,
    game: str,
    player_id: int,
    nickname: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    validate_fid: bool = True,
) -> dict[str, Any]:
    """Insert or update an external account row.

    When ``validate_fid`` is True (default), hits ``/api/player`` to confirm
    the fid exists in this game and to auto-populate ``nickname`` if the
    caller didn't supply one. The fid is rejected with ValueError on lookup
    failure — no row is written.
    """
    from century.api import CenturyAPIError, CenturyClient
    from century.games import get_game
    from config.giftcodes_db import (
        touch_external_gamer_seen,
        upsert_external_gamer,
    )

    resolved_nick = nickname or ""
    if validate_fid:
        client = CenturyClient(game=get_game(game))
        try:
            player = await client.fetch_player(player_id)
        except CenturyAPIError as exc:
            msg = f"fid {player_id} not found in {game}: {exc}"
            raise ValueError(msg) from exc
        if not resolved_nick:
            resolved_nick = player.nickname or ""

    row = upsert_external_gamer(
        player_id,
        game=game,
        nickname=resolved_nick or None,
        label=label,
        enabled=enabled,
    )
    if validate_fid:
        touch_external_gamer_seen(player_id, game=game)
        row = upsert_external_gamer(player_id, game=game)  # re-read with seen ts

    return {"ok": True, "account": _ext_to_dict(row)}


def toggle_external_account(
    *, game: str, player_id: int, enabled: bool
) -> dict[str, Any]:
    """Enable or disable an external account."""
    from config.giftcodes_db import set_external_gamer_enabled

    if not set_external_gamer_enabled(player_id, enabled, game=game):
        msg = f"external account not found: game={game} player_id={player_id}"
        raise KeyError(msg)
    return {"ok": True, "game": game, "player_id": player_id, "enabled": enabled}


def delete_external_account(*, game: str, player_id: int) -> dict[str, Any]:
    """Remove an external account."""
    from config.giftcodes_db import delete_external_gamer

    if not delete_external_gamer(player_id, game=game):
        msg = f"external account not found: game={game} player_id={player_id}"
        raise KeyError(msg)
    return {"ok": True, "game": game, "player_id": player_id}


def external_account_codes(player_id: int, *, game: str = "wos") -> dict[str, Any]:
    """Per-code redemption status for one external account (child table).

    Reads-only (mirrors :func:`list_external_accounts`).
    """
    from config.giftcodes_db import list_external_gamers

    pid = str(player_id)
    codes = list_codes(game=game)

    nickname = pid
    for ext in list_external_gamers(game=game):
        if str(ext.player_id) == pid:
            nickname = ext.nickname or pid
            break

    rows: list[dict[str, Any]] = []
    for code in codes:
        status = code.user_for.get(pid, RedeemStatus.PENDING)
        rows.append(
            {
                "code": code.name,
                "expires": code.expires.isoformat() if code.expires else "—",
                "slot_expired": code.is_effectively_expired(),
                "status": status.value,
                "redeemed": status.value in _REDEEMED,
                "needs_run": bool(
                    not code.is_effectively_expired() and code.needs_redemption(pid)
                ),
            }
        )

    redeemed = sum(1 for r in rows if r["redeemed"])
    needs_run = sum(1 for r in rows if r["needs_run"])
    return {
        "fid": pid,
        "nickname": nickname,
        "codes": rows,
        "summary": {"total": len(rows), "redeemed": redeemed, "needs_run": needs_run},
    }


async def stream_external_account_redeem(
    player_id: int, *, game: str = "wos"
) -> AsyncIterator[dict[str, Any]]:
    """Yield progress events while redeeming all pending codes for one account.

    Runs the single-account redeemer on a background task whose ``progress_cb``
    feeds an :class:`asyncio.Queue`; we drain the queue and yield events of the
    form ``{"type": "progress"|"done"|"error", ...}``. Caller (router) wraps
    each dict as an SSE ``data:`` frame.
    """
    import asyncio

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def _cb(done: int, total: int, message: str) -> None:
        queue.put_nowait(
            {"type": "progress", "done": done, "total": total, "message": message}
        )

    async def _run() -> None:
        try:
            summary = await redeem_gift_codes_for_player(
                player_id, game=game, progress_cb=_cb
            )
            attempted = len(getattr(summary, "results", []) or [])
            queue.put_nowait({"type": "done", "attempted": attempted})
        except Exception as exc:
            queue.put_nowait({"type": "error", "message": str(exc)})
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        await task
