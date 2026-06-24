"""Service layer for the alliance-broadcast catalog (CRUD + history + flags).

Validates/coerces operator input, persists through :mod:`modules.broadcast.db`,
and shapes responses for the dashboard. Pure-ish: no Redis, no device — the
catalog is plain SQLite the UI owns.
"""
from __future__ import annotations

import re
import time
from typing import Any

from layout.area_versions import compile_cond
from modules.broadcast import db, seed
from modules.broadcast.engine import cron_interval_seconds
from modules.broadcast.models import (
    CATEGORIES,
    MAX_TEXT_LEN,
    TRIGGER_CRON,
    TRIGGER_EVENT,
    VALID_SCOPES,
    VALID_TRIGGERS,
    BroadcastMessage,
)

# Curated suggestions for the event-trigger dropdown. These are the live-calendar
# flags (``event_<slug>``) the bot raises while an event runs, plus the armed
# reserve flag. The live set (from the read schedule) is merged in on top.
_COMMON_EVENT_FLAGS: tuple[tuple[str, str], ...] = (
    ("event_foundry_battle", "Foundry Battle"),
    ("event_bear_hunt", "Bear Hunt"),
    ("event_crazy_joe", "Crazy Joe"),
    ("joe_event_active", "Crazy Joe (armed, ±12h)"),
    ("event_canyon_clash", "Canyon Clash"),
    ("event_fishing_tournament", "Fishing Tournament"),
    ("event_state_of_power", "State of Power"),
    ("event_brothers_in_arms", "Brothers in Arms"),
)


class BroadcastValidationError(ValueError):
    """Raised on invalid operator input; the router maps it to HTTP 400."""


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return s or "message"


def _unique_id(title: str) -> str:
    base = _slug(title)
    existing = {m.id for m in db.list_messages()}
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _fail(message: str) -> None:
    raise BroadcastValidationError(message)


def _as_int(value: Any, *, field: str, default: int = 0, minimum: int | None = None) -> int:
    try:
        out = int(value) if value is not None and value != "" else default
    except (TypeError, ValueError) as exc:
        msg = f"{field} must be an integer"
        raise BroadcastValidationError(msg) from exc
    if minimum is not None and out < minimum:
        _fail(f"{field} must be >= {minimum}")
    return out


def _validate(msg: BroadcastMessage) -> None:
    if msg.game_scope not in VALID_SCOPES:
        _fail(f"scope must be one of {VALID_SCOPES}")
    if msg.trigger_kind not in VALID_TRIGGERS:
        _fail(f"trigger must be one of {VALID_TRIGGERS}")
    text = (msg.text or "").strip()
    if not text:
        _fail("text is required")
    if len(text) > MAX_TEXT_LEN:
        _fail(f"text must be <= {MAX_TEXT_LEN} characters")
    if msg.trigger_kind == TRIGGER_CRON and cron_interval_seconds(msg.cron) <= 0:
        _fail("cron must be '*/N * * * *' (every N min) or 'M */H * * *' (every H hours)")
    if msg.trigger_kind == TRIGGER_EVENT:
        cond = (msg.cond or "").strip()
        if not cond:
            _fail("event trigger needs a condition")
        try:
            compile_cond(cond)
        except SyntaxError as exc:
            msg_text = f"invalid condition: {exc}"
            raise BroadcastValidationError(msg_text) from exc


def _message_from_body(body: dict[str, Any]) -> BroadcastMessage:
    title = str(body.get("title") or "").strip()
    if not title:
        _fail("title is required")
    mid = str(body.get("id") or "").strip() or _unique_id(title)
    category = str(body.get("category") or "custom").strip() or "custom"
    if category not in CATEGORIES:
        category = "custom"
    trigger = str(body.get("trigger_kind") or TRIGGER_CRON).strip()
    return BroadcastMessage(
        id=mid,
        title=title,
        text=str(body.get("text") or ""),
        category=category,
        game_scope=str(body.get("game_scope") or "all").strip(),
        trigger_kind=trigger,
        cron=str(body.get("cron") or "").strip() if trigger == TRIGGER_CRON else "",
        cond=str(body.get("cond") or "").strip() if trigger == TRIGGER_EVENT else "",
        cooldown_minutes=_as_int(body.get("cooldown_minutes"), field="cooldown_minutes", default=0, minimum=0),
        priority=_as_int(body.get("priority"), field="priority", default=100),
        enabled=bool(body.get("enabled", True)),
    )


def _interval_label(msg: BroadcastMessage) -> str:
    """Human cadence/trigger summary for the dashboard row."""
    if msg.trigger_kind == TRIGGER_EVENT:
        return f"when {msg.cond}"
    secs = cron_interval_seconds(msg.cron)
    if secs <= 0:
        return msg.cron or "—"
    if secs % 3600 == 0:
        return f"every {secs // 3600}h"
    return f"every {secs // 60}m"


def _shape(msg: BroadcastMessage) -> dict[str, Any]:
    out = msg.to_dict()
    out["trigger_label"] = _interval_label(msg)
    return out


# ── public API ────────────────────────────────────────────────────────────────
def list_messages(*, game: str | None = None) -> dict[str, Any]:
    rows = db.list_messages(game=game)
    return {"game": game or "all", "messages": [_shape(m) for m in rows]}


def upsert_message(body: dict[str, Any]) -> dict[str, Any]:
    msg = _message_from_body(body)
    _validate(msg)
    saved = db.upsert_message(msg)
    return {"message": _shape(saved)}


def set_enabled(message_id: str, enabled: bool) -> dict[str, Any]:
    saved = db.set_enabled(message_id, enabled)
    if saved is None:
        raise KeyError(message_id)
    return {"message": _shape(saved)}


def delete_message(message_id: str) -> dict[str, Any]:
    if not db.delete_message(message_id):
        raise KeyError(message_id)
    return {"deleted": message_id}


def seed_defaults() -> dict[str, Any]:
    added = seed.seed_defaults()
    return {"added": added, "count": len(added)}


def history(*, game: str | None = None, alliance: str | None = None, limit: int = 50) -> dict[str, Any]:
    rows = db.recent_sends(game=game, alliance=alliance, limit=limit)
    return {
        "sends": [
            {
                "message_id": r.message_id,
                "game": r.game,
                "alliance": r.alliance,
                "fid": r.fid,
                "text": r.text,
                "sent_at": r.sent_at,
            }
            for r in rows
        ]
    }


def event_flags(*, game: str = "wos") -> dict[str, Any]:
    """Suggested ``event_<slug>`` flags for the event-trigger dropdown."""
    flags: dict[str, str] = dict(_COMMON_EVENT_FLAGS)
    try:
        from games.wos.core.calendar import db as cal_db
        from games.wos.core.calendar import schedule

        for state in cal_db.list_states(game=game):
            for row in cal_db.get_state_schedule(state, game=game):
                flag = schedule.event_flag(str(row.name))
                if flag:
                    flags.setdefault(flag, str(row.name))
    except Exception:
        pass
    return {"flags": [{"flag": f, "label": label} for f, label in sorted(flags.items())]}


def now() -> float:
    return time.time()
