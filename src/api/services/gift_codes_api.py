"""Gift codes dashboard data."""
from __future__ import annotations

from typing import Any

import yaml
from modules.gift_codes.models import GiftCodeDB, RedeemStatus
from modules.gift_codes.redeemer import run_gift_code_redeemer
from modules.gift_codes.scraper import poll_once

from config.devices import load_devices
from config.paths import repo_root

_REPO = repo_root()
_CODES_PATH = _REPO / "db" / "giftCodes.yaml"
_DEVICES_PATH = _REPO / "db" / "devices.yaml"

_REDEEMED = frozenset({RedeemStatus.SUCCESS.value, RedeemStatus.ALREADY_RECEIVED.value})


def _load_db() -> tuple[GiftCodeDB, str | None]:
    if not _CODES_PATH.is_file():
        return GiftCodeDB(), None
    raw = yaml.safe_load(_CODES_PATH.read_text(encoding="utf-8")) or {}
    try:
        return GiftCodeDB.model_validate(raw), None
    except Exception as exc:
        return GiftCodeDB(), str(exc)


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
    db, parse_error = _load_db()
    registry = load_devices(_DEVICES_PATH)
    player_ids = list(dict.fromkeys(registry.all_player_ids()))
    for c in db.codes:
        for pid in c.user_for:
            if pid not in player_ids:
                player_ids.append(pid)

    q = query.strip().lower()
    active: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    pending_slots = 0
    needs_run_count = 0
    redeemed_slots = 0

    for code in db.codes:
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
        "codes_path": str(_CODES_PATH.relative_to(_REPO)),
        "devices_path": str(_DEVICES_PATH.relative_to(_REPO)),
        "parse_error": parse_error,
        "missing_codes_file": not _CODES_PATH.is_file(),
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
    new = await poll_once(_CODES_PATH)
    return {"ok": True, "new_codes": new, "count": len(new)}


async def redeem_gift_codes() -> dict[str, Any]:
    if not _CODES_PATH.is_file():
        msg = "missing giftCodes.yaml"
        raise FileNotFoundError(msg)
    if not _DEVICES_PATH.is_file():
        msg = "missing devices.yaml"
        raise FileNotFoundError(msg)
    await run_gift_code_redeemer(_CODES_PATH, _DEVICES_PATH)
    return {"ok": True}
