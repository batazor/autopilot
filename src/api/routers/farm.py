"""Farm registration handoff API (R5 / owner-only).

Backs the ``/farm`` dashboard page: starts the browser registration helper,
surfaces which generated account is awaiting the operator's final captcha/submit
handoff, and relays manual aborts back to the registration process (see
``games.wos.farm.register`` + ``dashboard.farm_handoff``).
Passwords are never returned over the API.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import redis  # noqa: TC002 — FastAPI resolves the Depends annotation at runtime
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_redis
from config import farm_accounts_db
from config.loader import load_settings
from config.paths import repo_root
from config.state_store import get_state_store
from dashboard import farm_handoff
from dashboard.redis_client import get_instance_state

router = APIRouter(prefix="/api/farm", tags=["farm"])
_registration_proc: subprocess.Popen[bytes] | None = None
_registration_started_at: float | None = None
_registration_finished_at: float | None = None
_registration_exit_code: int | None = None
_registration_log_path: str | None = None
_registration_log_handle: Any | None = None


class DoneBody(BaseModel):
    username: str
    outcome: str = "failed"  # legacy endpoint also accepts "done"


class GenerateBody(BaseModel):
    count: int = 1
    seed: str | None = None
    server: str = "wos_beta"


class StartRegistrationBody(BaseModel):
    username: str | None = None
    seed: str | None = None
    server: str = "wos_beta"
    headless: bool = False
    existing: bool = False


class CharacterBody(BaseModel):
    server: str
    fid: str
    nickname: str = ""
    note: str = ""


class BindBody(BaseModel):
    device_serial: str


class RoleBody(BaseModel):
    role: str


class OptionBody(BaseModel):
    key: str
    value: Any


class DeleteAccountBody(BaseModel):
    confirm_username: str


_MAX_GENERATE = 50
_DELETE_ACCOUNT_BODY = Body(default=None)
_LOG_TAIL_CHARS = 8000


def _character_role(fid: str) -> str:
    """The per-character planner role, read from per-gamer state (``planner.role``).

    Falls back to the system default when the character has no stored role yet —
    matching ``roles.get_role(None)``. The per-gamer store is keyed by fid.
    """
    from games.wos.core.roles import DEFAULT_ROLE_ID

    if not fid:
        return DEFAULT_ROLE_ID
    gamer = get_state_store().get(str(fid))
    if gamer is None:
        return DEFAULT_ROLE_ID
    return str(gamer.get("planner.role") or DEFAULT_ROLE_ID)


def _active_players(client: redis.Redis) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    try:
        instances = load_settings().instances
    except Exception:
        instances = []
    for inst in instances:
        iid = str(getattr(inst, "instance_id", "") or "").strip()
        if not iid:
            continue
        try:
            row = get_instance_state(client, iid)
        except Exception:
            continue
        active = (row.get("active_player") or "").strip()
        if not active or active == "—":
            continue
        item = out.setdefault(active, {"fid": active, "instances": []})
        item["instances"].append(
            {
                "instance_id": iid,
                "screen": (row.get("current_screen") or "").strip(),
                "task": (
                    row.get("current_scenario") or row.get("current_task_type") or ""
                ).strip(),
            }
        )
    return out


def _close_registration_log_handle() -> None:
    global _registration_log_handle
    if _registration_log_handle is None:
        return
    try:
        _registration_log_handle.close()
    finally:
        _registration_log_handle = None


def _registration_log_file() -> str:
    root = repo_root()
    log_dir = root / "temporal"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / "farm_registration.log")


def _read_registration_log_tail() -> str:
    path = _registration_log_path or _registration_log_file()
    if not Path(path).exists():
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # Avoid putting generated credentials into the dashboard log panel.
    lines = []
    for line in text[-_LOG_TAIL_CHARS:].splitlines():
        if "password:" in line:
            before, _, after = line.partition("password:")
            if "(status:" in after:
                _, _, suffix = after.partition("(status:")
                line = f"{before}password: ***  (status:{suffix}"
            else:
                line = f"{before}password: ***"
        elif "пароль:" in line:
            before, _, after = line.partition("пароль:")
            if "(статус:" in after:
                _, _, suffix = after.partition("(статус:")
                line = f"{before}password: ***  (status:{suffix}"
            else:
                line = f"{before}password: ***"
        lines.append(line)
    return "\n".join(lines)


def _registration_process_running() -> bool:
    global _registration_exit_code, _registration_finished_at, _registration_proc
    if _registration_proc is None:
        return False
    code = _registration_proc.poll()
    if code is None:
        return True
    if _registration_exit_code is None:
        _registration_exit_code = code
        _registration_finished_at = time.time()
        _close_registration_log_handle()
    return False


def _start_registration_process(body: StartRegistrationBody) -> dict[str, Any]:
    global _registration_exit_code, _registration_finished_at, _registration_log_handle
    global _registration_log_path, _registration_proc, _registration_started_at
    cmd = [sys.executable, "-m", "games.wos.farm.register", "--ui"]
    username = (body.username or "").strip()
    seed = (body.seed or "").strip()
    server = (body.server or "").strip() or "wos_beta"
    if username:
        cmd.extend(["--username", username])
    if body.existing:
        cmd.append("--existing")
    if seed:
        cmd.extend(["--seed", seed])
    if server:
        cmd.extend(["--server", server])
    if body.headless:
        cmd.append("--headless")

    _close_registration_log_handle()
    _registration_exit_code = None
    _registration_finished_at = None
    _registration_log_path = _registration_log_file()
    log = Path(_registration_log_path).open("wb", buffering=0)  # noqa: SIM115
    _registration_log_handle = log
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"[{started}] farm registration starting\n".encode())
    log.write(("cmd: " + " ".join(cmd) + "\n\n").encode())
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        cwd=repo_root(),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _registration_proc = proc
    _registration_started_at = time.time()
    return {
        "running": True,
        "pid": proc.pid,
        "started_at": _registration_started_at,
        "log_path": _registration_log_path,
    }


@router.get("/registration/pending")
def get_pending(
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    return {"pending": farm_handoff.get_pending(client)}


@router.get("/registration/status")
def get_registration_status(
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    pending = farm_handoff.get_pending(client)
    running = _registration_process_running()
    exit_code = None if running else _registration_exit_code
    return {
        "running": running,
        "pending": pending,
        "pid": _registration_proc.pid if _registration_proc is not None else None,
        "started_at": _registration_started_at,
        "finished_at": _registration_finished_at,
        "exit_code": exit_code,
        "log_path": _registration_log_path,
        "log_tail": _read_registration_log_tail(),
    }


@router.delete("/registration/log")
def clear_registration_log(
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    global _registration_exit_code, _registration_finished_at, _registration_log_path
    global _registration_proc, _registration_started_at
    pending = farm_handoff.get_pending(client)
    if pending or _registration_process_running():
        raise HTTPException(
            status_code=409,
            detail="registration is still active",
        )

    _close_registration_log_handle()
    path = _registration_log_path or _registration_log_file()
    Path(path).unlink(missing_ok=True)
    _registration_proc = None
    _registration_started_at = None
    _registration_finished_at = None
    _registration_exit_code = None
    _registration_log_path = None
    return {"ok": True}


@router.post("/registration/start")
def post_start_registration(
    body: StartRegistrationBody,
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    pending = farm_handoff.get_pending(client)
    if pending:
        return {
            "running": True,
            "pending": pending,
            "pid": _registration_proc.pid if _registration_process_running() else None,
            "started_at": _registration_started_at,
        }
    if _registration_process_running():
        return {
            "running": True,
            "pending": None,
            "pid": _registration_proc.pid if _registration_proc is not None else None,
            "started_at": _registration_started_at,
            "log_path": _registration_log_path,
        }
    try:
        return {**_start_registration_process(body), "pending": None}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/registration/done")
def post_done(
    body: DoneBody,
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    try:
        farm_handoff.signal(client, username, body.outcome)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "username": username, "outcome": body.outcome.strip().lower()}


@router.get("/accounts")
def list_accounts(
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    active_by_fid = _active_players(client)
    return {
        "accounts": [
            {
                "username": a.username,
                "status": a.status,
                "server": a.server,
                "device_serial": a.device_serial,
                "created_at": a.created_at,
                "registered_at": a.registered_at,
                "active": next(
                    (
                        active_by_fid[c.fid]
                        for c in a.characters
                        if c.fid in active_by_fid
                    ),
                    None,
                ),
                "characters": [
                    {
                        "server": c.server,
                        "fid": c.fid,
                        "nickname": c.nickname,
                        "created_at": c.created_at,
                        "updated_at": c.updated_at,
                        "note": c.note,
                        "active": active_by_fid.get(c.fid),
                        "role": _character_role(c.fid),
                    }
                    for c in a.characters
                ],
            }
            for a in farm_accounts_db.list_accounts(game="wos")
        ],
        "active": list(active_by_fid.values()),
    }


@router.post("/generate")
def post_generate(body: GenerateBody) -> dict[str, Any]:
    from games.wos.farm import generator

    if body.count < 1 or body.count > _MAX_GENERATE:
        raise HTTPException(
            status_code=400, detail=f"count must be 1..{_MAX_GENERATE}"
        )
    created = generator.generate_and_store(
        body.count, seed=body.seed, server=body.server.strip() or "wos_beta"
    )
    return {"created": [a.username for a in created]}


@router.get("/accounts/{username}/secret")
def get_secret(username: str) -> dict[str, Any]:
    """Reveal a single account's password (owner-only, on demand — not in the list)."""
    acct = farm_accounts_db.get_account(username, game="wos")
    if acct is None:
        raise HTTPException(status_code=404, detail="account not found")
    return {"username": acct.username, "password": acct.password}


@router.post("/accounts/{username}/characters")
def post_character(username: str, body: CharacterBody) -> dict[str, Any]:
    try:
        character = farm_accounts_db.upsert_character(
            username,
            server=body.server,
            fid=body.fid,
            game="wos",
            nickname=body.nickname,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if character is None:
        raise HTTPException(status_code=404, detail="account not found")
    return {
        "ok": True,
        "character": {
            "server": character.server,
            "fid": character.fid,
            "nickname": character.nickname,
            "created_at": character.created_at,
            "updated_at": character.updated_at,
            "note": character.note,
        },
    }


@router.delete("/accounts/{username}/characters/{server}")
def delete_character(username: str, server: str) -> dict[str, Any]:
    if not farm_accounts_db.delete_character(username, server=server, game="wos"):
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.post("/accounts/{username}/bind")
def post_bind(username: str, body: BindBody) -> dict[str, Any]:
    serial = body.device_serial.strip()
    if not serial:
        raise HTTPException(status_code=400, detail="device_serial required")
    if not farm_accounts_db.bind_device(username, serial, game="wos"):
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}


@router.get("/roles")
def list_roles() -> dict[str, Any]:
    """The selectable planner role profiles (id + human labels) for the UI picker."""
    from games.wos.core.roles import ROLES

    return {
        "roles": [
            {"id": r.id, "label": r.label, "description": r.description}
            for r in ROLES.values()
        ]
    }


@router.post("/accounts/{username}/characters/{fid}/role")
def post_character_role(username: str, fid: str, body: RoleBody) -> dict[str, Any]:
    """Set a character's planner role (stored as per-gamer ``planner.role``)."""
    from games.wos.core.roles import ROLES

    role = body.role.strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {body.role!r}")
    acct = farm_accounts_db.get_account(username, game="wos")
    if acct is None or not any(c.fid == fid for c in acct.characters):
        raise HTTPException(status_code=404, detail="character not found")
    if not fid.isdigit():
        raise HTTPException(status_code=400, detail="character has no numeric fid")
    get_state_store().get_or_create(fid).set("planner.role", role)
    return {"username": username, "fid": fid, "role": role}


def _require_character(username: str, fid: str) -> None:
    """404/400 unless ``fid`` is a numeric character of ``username``."""
    acct = farm_accounts_db.get_account(username, game="wos")
    if acct is None or not any(c.fid == fid for c in acct.characters):
        raise HTTPException(status_code=404, detail="character not found")
    if not fid.isdigit():
        raise HTTPException(status_code=400, detail="character has no numeric fid")


@router.get("/accounts/{username}/characters/{fid}/options")
def get_character_options(username: str, fid: str) -> dict[str, Any]:
    """All per-account options + this character's current values (registry-driven).

    Adding an option to ``account_options.ACCOUNT_OPTIONS`` makes it appear here
    automatically — no new endpoint.
    """
    from games.wos.core import account_options

    _require_character(username, fid)
    gamer = get_state_store().get(str(fid))
    flat = gamer.to_flat_dict() if gamer is not None else {}
    rows = account_options.options_for_state(flat)
    return {"username": username, "fid": fid, "options": rows}


@router.post("/accounts/{username}/characters/{fid}/options")
def set_character_option(username: str, fid: str, body: OptionBody) -> dict[str, Any]:
    """Set one per-account option, validated + coerced against the registry.

    Persists to per-gamer state.
    """
    from games.wos.core import account_options

    _require_character(username, fid)
    option = account_options.option_by_key(body.key)
    if option is None:
        raise HTTPException(status_code=404, detail=f"unknown option: {body.key!r}")
    try:
        value = account_options.coerce_value(option, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_state_store().get_or_create(fid).set(option.key, value)
    return {"username": username, "fid": fid, "key": option.key, "value": value}


@router.delete("/accounts/{username}")
def delete_account(
    username: str,
    body: DeleteAccountBody | None = _DELETE_ACCOUNT_BODY,
) -> dict[str, Any]:
    if body is None or body.confirm_username.strip() != username:
        raise HTTPException(
            status_code=400,
            detail="confirm_username must match username",
        )
    if not farm_accounts_db.delete_account(username, game="wos"):
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}
