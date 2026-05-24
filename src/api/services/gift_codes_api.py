"""Gift codes dashboard data."""
from __future__ import annotations

from typing import Any

from modules.gift_codes.models import RedeemStatus
from modules.gift_codes.redeemer import run_gift_code_redeemer
from modules.gift_codes.scraper import poll_once

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


def build_gift_codes_view(*, query: str = "") -> dict[str, Any]:
    codes = list_codes()
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
