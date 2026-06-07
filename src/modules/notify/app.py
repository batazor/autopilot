"""FastAPI web UI + JSON API for the notification monitor."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from . import adb_reader, config, db
from .logging_setup import get_logger
from .service import MonitorService

log = get_logger("app")

# Single shared monitor instance (background thread + Redis publisher).
monitor = MonitorService()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    monitor.start()
    log.info("notify_monitor API started")
    yield
    monitor.stop()


app = FastAPI(title="Notification Monitor", version="0.1.0", lifespan=lifespan)


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
    scenario: str = ""


class PatternPatch(BaseModel):
    game: str | None = None
    pattern_regex: str | None = None
    event_type: str | None = None
    description: str | None = None
    active: bool | None = None
    scenario: str | None = None


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
    if game not in config.GAMES:
        raise HTTPException(400, f"Unknown game '{game}'. Known: {list(config.GAMES)}")


def _validate_regex(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise HTTPException(400, f"Invalid regex: {exc}") from exc


# --- UI --------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


# --- meta / status ---------------------------------------------------------

@app.get("/api/games")
def api_games() -> list[dict[str, Any]]:
    return [{"id": g.id, "name": g.name, "packages": list(g.packages)} for g in config.GAMES.values()]


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return {
        "monitor": monitor.status(),
        "counts": db.counts(),
        "adb_devices": adb_reader.list_devices(db.get_setting("adb_path", "adb") or "adb"),
    }


@app.post("/api/poll")
def api_poll() -> dict[str, Any]:
    """Trigger a single poll cycle on demand (useful for testing/UI button)."""
    try:
        return {"ok": True, "summary": monitor.poll_once()}
    except adb_reader.AdbError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/monitor/{action}")
def api_monitor(action: str) -> dict[str, Any]:
    if action == "start":
        db.set_setting("monitor_enabled", "1")
        monitor.start()
    elif action == "stop":
        db.set_setting("monitor_enabled", "0")
    else:
        raise HTTPException(400, "action must be 'start' or 'stop'")
    return {"ok": True, "running": monitor.running}


# --- events ----------------------------------------------------------------

@app.get("/api/events")
def api_events(limit: int = 100, game: str | None = None, player: str | None = None) -> list[dict[str, Any]]:
    return db.list_events(limit=limit, game=game, player=player)


# --- players ---------------------------------------------------------------

@app.get("/api/players")
def api_players(game: str | None = None) -> list[dict[str, Any]]:
    return db.list_players(game=game)


@app.post("/api/players")
def api_add_player(body: PlayerIn) -> dict[str, Any]:
    _validate_game(body.game)
    if not body.nickname.strip():
        raise HTTPException(400, "nickname required")
    pid = db.add_player(body.nickname, body.game, body.active)
    return {"ok": True, "id": pid}


@app.patch("/api/players/{player_id}")
def api_patch_player(player_id: int, body: PlayerPatch) -> dict[str, Any]:
    db.set_player_active(player_id, body.active)
    return {"ok": True}


@app.delete("/api/players/{player_id}")
def api_delete_player(player_id: int) -> dict[str, Any]:
    db.delete_player(player_id)
    return {"ok": True}


# --- patterns --------------------------------------------------------------

@app.get("/api/patterns")
def api_patterns(game: str | None = None) -> list[dict[str, Any]]:
    return db.list_patterns(game=game)


@app.post("/api/patterns")
def api_add_pattern(body: PatternIn) -> dict[str, Any]:
    _validate_game(body.game)
    _validate_regex(body.pattern_regex)
    if not body.event_type.strip():
        raise HTTPException(400, "event_type required")
    pid = db.add_pattern(
        body.game, body.pattern_regex, body.event_type, body.description,
        body.active, body.scenario,
    )
    monitor.matcher.refresh()
    return {"ok": True, "id": pid}


@app.patch("/api/patterns/{pattern_id}")
def api_patch_pattern(pattern_id: int, body: PatternPatch) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if "game" in fields:
        _validate_game(fields["game"])
    if "pattern_regex" in fields:
        _validate_regex(fields["pattern_regex"])
    db.update_pattern(pattern_id, **fields)
    monitor.matcher.refresh()
    return {"ok": True}


@app.delete("/api/patterns/{pattern_id}")
def api_delete_pattern(pattern_id: int) -> dict[str, Any]:
    db.delete_pattern(pattern_id)
    monitor.matcher.refresh()
    return {"ok": True}


@app.post("/api/patterns/test")
def api_test_pattern(body: PatternTest) -> dict[str, Any]:
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

@app.get("/api/unrecognized")
def api_unrecognized(include_reviewed: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    return db.list_unrecognized(limit=limit, include_reviewed=include_reviewed)


@app.post("/api/unrecognized/{notif_id}/review")
def api_review(notif_id: int, reviewed: bool = True) -> dict[str, Any]:
    db.set_unrecognized_reviewed(notif_id, reviewed)
    return {"ok": True}


@app.post("/api/unrecognized/{notif_id}/promote")
def api_promote(notif_id: int, body: PromoteIn) -> dict[str, Any]:
    notif = db.get_unrecognized(notif_id)
    if not notif:
        raise HTTPException(404, "notification not found")
    _validate_regex(body.pattern_regex)
    pid = db.add_pattern(notif["game"], body.pattern_regex, body.event_type, body.description, True)
    db.set_unrecognized_reviewed(notif_id, True)
    monitor.matcher.refresh()
    return {"ok": True, "pattern_id": pid}


# --- settings --------------------------------------------------------------

@app.get("/api/settings")
def api_get_settings() -> dict[str, str]:
    return db.get_all_settings()


@app.put("/api/settings")
def api_put_settings(body: SettingsIn) -> dict[str, Any]:
    if body.poll_interval is not None:
        if body.poll_interval < 1:
            raise HTTPException(400, "poll_interval must be >= 1")
        db.set_setting("poll_interval", str(body.poll_interval))
    if body.adb_serial is not None:
        db.set_setting("adb_serial", body.adb_serial)
    if body.adb_path is not None:
        db.set_setting("adb_path", body.adb_path)
    if body.monitor_enabled is not None:
        db.set_setting("monitor_enabled", "1" if body.monitor_enabled else "0")
        if body.monitor_enabled:
            monitor.start()
    return {"ok": True, "settings": db.get_all_settings()}


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover - safety net
    log.exception("Unhandled API error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})
