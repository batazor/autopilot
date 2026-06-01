"""Notification monitor routes — back the Debug ▸ Notify monitor page.

Reuses the standalone :mod:`notify_monitor` package (SQLite data layer, dumpsys
parser, Redis publisher, background poll loop) but serves it through the main
dashboard API under ``/api/notify/*`` so it travels with the bot instead of a
separate :8800 process. The background poll thread is started once on import
and honours the ``monitor_enabled`` setting, exactly like the headless service.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from notify_monitor import config as nm_config
from notify_monitor import db as nm_db
from notify_monitor.service import MonitorService
from pydantic import BaseModel

router = APIRouter(prefix="/api/notify", tags=["notify"])

# One shared monitor (background thread + Redis publisher) for this API process.
nm_db.init_db()
_monitor = MonitorService()
_monitor.start()


# --- request models --------------------------------------------------------

class PlayerIn(BaseModel):
    nickname: str
    game: str
    active: bool = True


class PlayerPatch(BaseModel):
    active: bool


class PatternIn(BaseModel):
    game: str
    pattern_regex: str
    event_type: str
    description: str = ""
    active: bool = True


class PatternPatch(BaseModel):
    game: str | None = None
    pattern_regex: str | None = None
    event_type: str | None = None
    description: str | None = None
    active: bool | None = None


class PatternTest(BaseModel):
    pattern_regex: str
    sample_text: str


class PromoteIn(BaseModel):
    pattern_regex: str
    event_type: str
    description: str = ""


class SettingsIn(BaseModel):
    poll_interval: int | None = None
    adb_serial: str | None = None
    adb_path: str | None = None
    monitor_enabled: bool | None = None


def _validate_game(game: str) -> None:
    if game not in nm_config.GAMES:
        raise HTTPException(400, f"Unknown game '{game}'. Known: {list(nm_config.GAMES)}")


def _validate_regex(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise HTTPException(400, f"Invalid regex: {exc}") from exc


# --- meta / status ---------------------------------------------------------

@router.get("/games")
def list_games() -> list[dict[str, Any]]:
    return [
        {"id": g.id, "name": g.name, "packages": list(g.packages)}
        for g in nm_config.GAMES.values()
    ]


@router.get("/status")
def get_status() -> dict[str, Any]:
    from notify_monitor import adb_reader

    return {
        "monitor": _monitor.status(),
        "counts": nm_db.counts(),
        "adb_devices": adb_reader.list_devices(nm_db.get_setting("adb_path", "adb") or "adb"),
    }


@router.post("/poll")
def poll_now() -> dict[str, Any]:
    from notify_monitor import adb_reader

    try:
        return {"ok": True, "summary": _monitor.poll_once()}
    except adb_reader.AdbError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/monitor/{action}")
def set_monitor(action: str) -> dict[str, Any]:
    if action == "start":
        nm_db.set_setting("monitor_enabled", "1")
        _monitor.start()
    elif action == "stop":
        nm_db.set_setting("monitor_enabled", "0")
    else:
        raise HTTPException(400, "action must be 'start' or 'stop'")
    return {"ok": True, "running": _monitor.running}


# --- events ----------------------------------------------------------------

@router.get("/events")
def list_events(limit: int = 100, game: str | None = None, player: str | None = None) -> list[dict[str, Any]]:
    return nm_db.list_events(limit=limit, game=game, player=player)


# --- players ---------------------------------------------------------------

@router.get("/players")
def list_players(game: str | None = None) -> list[dict[str, Any]]:
    return nm_db.list_players(game=game)


@router.post("/players")
def add_player(body: PlayerIn) -> dict[str, Any]:
    _validate_game(body.game)
    if not body.nickname.strip():
        raise HTTPException(400, "nickname required")
    return {"ok": True, "id": nm_db.add_player(body.nickname, body.game, body.active)}


@router.patch("/players/{player_id}")
def patch_player(player_id: int, body: PlayerPatch) -> dict[str, Any]:
    nm_db.set_player_active(player_id, body.active)
    return {"ok": True}


@router.delete("/players/{player_id}")
def delete_player(player_id: int) -> dict[str, Any]:
    nm_db.delete_player(player_id)
    return {"ok": True}


# --- patterns --------------------------------------------------------------

@router.get("/patterns")
def list_patterns(game: str | None = None) -> list[dict[str, Any]]:
    return nm_db.list_patterns(game=game)


@router.post("/patterns")
def add_pattern(body: PatternIn) -> dict[str, Any]:
    _validate_game(body.game)
    _validate_regex(body.pattern_regex)
    if not body.event_type.strip():
        raise HTTPException(400, "event_type required")
    pid = nm_db.add_pattern(body.game, body.pattern_regex, body.event_type, body.description, body.active)
    _monitor.matcher.refresh()
    return {"ok": True, "id": pid}


@router.patch("/patterns/{pattern_id}")
def patch_pattern(pattern_id: int, body: PatternPatch) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if "game" in fields:
        _validate_game(fields["game"])
    if "pattern_regex" in fields:
        _validate_regex(fields["pattern_regex"])
    nm_db.update_pattern(pattern_id, **fields)
    _monitor.matcher.refresh()
    return {"ok": True}


@router.delete("/patterns/{pattern_id}")
def delete_pattern(pattern_id: int) -> dict[str, Any]:
    nm_db.delete_pattern(pattern_id)
    _monitor.matcher.refresh()
    return {"ok": True}


@router.post("/patterns/test")
def test_pattern(body: PatternTest) -> dict[str, Any]:
    try:
        rex = re.compile(body.pattern_regex, re.IGNORECASE)
    except re.error as exc:
        return {"ok": False, "error": str(exc)}
    m = rex.search(body.sample_text)
    return {
        "ok": True,
        "matched": bool(m),
        "match": m.group(0) if m else None,
        "groups": m.groupdict() if m else {},
    }


# --- unrecognized ----------------------------------------------------------

@router.get("/unrecognized")
def list_unrecognized(include_reviewed: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    return nm_db.list_unrecognized(limit=limit, include_reviewed=include_reviewed)


@router.post("/unrecognized/{notif_id}/review")
def review_unrecognized(notif_id: int, reviewed: bool = True) -> dict[str, Any]:
    nm_db.set_unrecognized_reviewed(notif_id, reviewed)
    return {"ok": True}


@router.post("/unrecognized/{notif_id}/promote")
def promote_unrecognized(notif_id: int, body: PromoteIn) -> dict[str, Any]:
    notif = nm_db.get_unrecognized(notif_id)
    if not notif:
        raise HTTPException(404, "notification not found")
    _validate_regex(body.pattern_regex)
    pid = nm_db.add_pattern(notif["game"], body.pattern_regex, body.event_type, body.description, True)
    nm_db.set_unrecognized_reviewed(notif_id, True)
    _monitor.matcher.refresh()
    return {"ok": True, "pattern_id": pid}


# --- settings --------------------------------------------------------------

@router.get("/settings")
def get_settings() -> dict[str, str]:
    return nm_db.get_all_settings()


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    if body.poll_interval is not None:
        if body.poll_interval < 1:
            raise HTTPException(400, "poll_interval must be >= 1")
        nm_db.set_setting("poll_interval", str(body.poll_interval))
    if body.adb_serial is not None:
        nm_db.set_setting("adb_serial", body.adb_serial)
    if body.adb_path is not None:
        nm_db.set_setting("adb_path", body.adb_path)
    if body.monitor_enabled is not None:
        nm_db.set_setting("monitor_enabled", "1" if body.monitor_enabled else "0")
        if body.monitor_enabled:
            _monitor.start()
    return {"ok": True, "settings": nm_db.get_all_settings()}
