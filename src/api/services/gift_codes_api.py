"""Gift codes dashboard data."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from century.gift_codes.models import RedeemStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
from century.gift_codes.wos import poll_once, run_gift_code_redeemer
from config.devices import load_devices
from config.giftcodes_db import list_codes
from config.paths import repo_root
from config.state_sqlite import state_db_path

_REPO = repo_root()

_REDEEMED = frozenset({RedeemStatus.SUCCESS.value, RedeemStatus.ALREADY_RECEIVED.value})


def _status_token(cell: str) -> str:
    raw = str(cell or "").strip()
    if not raw or raw == "—":
        return ""
    return raw.split(" ", 1)[0].strip()


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
    codes = list_codes(game=game)
    registry = load_devices()
    player_ids = list(dict.fromkeys(registry.all_player_ids()))
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
        "codes_db": str(state_db_path().relative_to(_REPO)),
        "devices_path": str(state_db_path().relative_to(_REPO)),
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


async def scrape_gift_codes() -> dict[str, Any]:
    new = await poll_once()
    return {"ok": True, "new_codes": new, "count": len(new)}


async def redeem_gift_codes() -> dict[str, Any]:
    await run_gift_code_redeemer()
    return {"ok": True}


# ---------------------------------------------------------------------------
# External accounts service layer
#
# Three layers of feature gating per the multi-game-migration plan §4.8:
#   1. UI (web/) hides controls when feature absent.
#   2. API (this layer + router) rejects writes with 402.
#   3. Redeemer (games/*/gift_codes/redeemer.py) drops external rows from
#      the redeem pass — defends against stale rows after license downgrade.
#
# Reads are NOT gated — a downgraded license still shows the existing rows
# read-only so the operator sees what they've lost access to.
# ---------------------------------------------------------------------------

_EXTERNAL_FEATURE = "gift_codes.external_accounts"


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
    """Read all external accounts for ``game``. Always allowed."""
    from config.giftcodes_db import list_external_gamers
    from licensing.gate import has_feature

    rows = list_external_gamers(game=game)
    return {
        "game": game,
        "feature_licensed": has_feature(_EXTERNAL_FEATURE),
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
    """Insert or update an external account row. Requires Pro feature.

    When ``validate_fid`` is True (default), hits ``/api/player`` to confirm
    the fid exists in this game and to auto-populate ``nickname`` if the
    caller didn't supply one. The fid is rejected with ValueError on lookup
    failure — no row is written.
    """
    from century.api import CenturyAPIError, CenturyClient
    from century.games import get_game
    from config.giftcodes_db import touch_external_gamer_seen, upsert_external_gamer
    from licensing.gate import require_feature

    require_feature(_EXTERNAL_FEATURE)  # raises LicenseError → 402

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
    """Enable or disable an external account. Requires Pro feature."""
    from config.giftcodes_db import set_external_gamer_enabled
    from licensing.gate import require_feature

    require_feature(_EXTERNAL_FEATURE)

    if not set_external_gamer_enabled(player_id, enabled, game=game):
        msg = f"external account not found: game={game} player_id={player_id}"
        raise KeyError(msg)
    return {"ok": True, "game": game, "player_id": player_id, "enabled": enabled}


def delete_external_account(*, game: str, player_id: int) -> dict[str, Any]:
    """Remove an external account. Requires Pro feature."""
    from config.giftcodes_db import delete_external_gamer
    from licensing.gate import require_feature

    require_feature(_EXTERNAL_FEATURE)

    if not delete_external_gamer(player_id, game=game):
        msg = f"external account not found: game={game} player_id={player_id}"
        raise KeyError(msg)
    return {"ok": True, "game": game, "player_id": player_id}


def require_external_accounts_feature() -> None:
    """Raise ``LicenseError`` unless the external-accounts feature is licensed.

    Lets the router gate the SSE redeem endpoint *before* the stream starts, so
    an unlicensed caller gets a clean 402 instead of a half-open event stream.
    """
    from licensing.gate import require_feature

    require_feature(_EXTERNAL_FEATURE)


def external_account_codes(player_id: int, *, game: str = "wos") -> dict[str, Any]:
    """Per-code redemption status for one external account (child table).

    Reads-only and always allowed (mirrors :func:`list_external_accounts`), so
    the status table renders even for read-only rows after a license downgrade.
    """
    from config.giftcodes_db import list_external_gamers
    from licensing.gate import has_feature

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
        "feature_licensed": has_feature(_EXTERNAL_FEATURE),
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

    from century.gift_codes.wos import run_gift_code_redeemer_for_player

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def _cb(done: int, total: int, message: str) -> None:
        queue.put_nowait(
            {"type": "progress", "done": done, "total": total, "message": message}
        )

    async def _run() -> None:
        try:
            summary = await run_gift_code_redeemer_for_player(player_id, progress_cb=_cb)
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
