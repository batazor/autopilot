"""Player state view models — shared by Streamlit UI and FastAPI (no Streamlit deps)."""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from config.state_schema import GamerState, StateDB

from century.api import CenturyAPIError, CenturyClient
from config.buildings import get_building_registry
from config.devices import load_devices, upsert_device_gamer
from config.heroes import HeroDef, get_hero_registry
from config.loader import load_settings
from config.paths import repo_root
from config.state_sqlite import get_player_stats, load_state_db_raw, state_db_path
from config.state_store import get_state_store

_LEVEL_PREFIX = "buildings.levels."


def state_db_file_path() -> Path:
    return state_db_path()


def state_yaml_path() -> Path:
    """Legacy alias — persisted state lives in SQLite."""
    return state_db_path()


def load_state_db() -> tuple[StateDB | None, str | None, str]:
    db, err, raw = load_state_db_raw()
    if err:
        return None, err, raw
    return db, None, raw


def list_known_player_ids() -> list[str]:
    db, _, _ = load_state_db()
    yaml_ids = {str(g.id) for g in db.gamers} if db else set()
    devices = load_devices()
    return sorted(set(devices.all_player_ids()) | yaml_ids)


def infer_instance_id_for_player(player_id: str) -> str:
    try:
        reg = load_devices()
        dev = reg.get_device_for_player(player_id)
        if dev is not None and dev.name.strip():
            return dev.name.strip()
    except Exception:
        pass
    try:
        settings = load_settings()
        if settings.instances:
            return settings.instances[0].instance_id
    except Exception:
        pass
    return ""


def gamer_summary_row(g: GamerState) -> dict[str, Any]:
    b = g.buildings
    hud = b.state.text or ""
    bldg_hud = (hud[:40] + "…") if len(hud) > 40 else (hud or "—")
    return {
        "id": g.id,
        "nickname": g.nickname or "—",
        "power": g.power,
        "gems": g.gems,
        "furnace_level": b.furnace.level,
        "furnace_power": b.furnace.power,
        "building_hud": bldg_hud,
        "queue1": b.queue1 or "—",
        "queue2": b.queue2 or "—",
        "alliance": g.alliance.name or "—",
        "alliance_power": g.alliance.power,
        "exploration_level": g.exploration.level,
        "exploration_power": g.exploration.state.myPower,
        "arena_rank": g.arena.rank,
        "arena_power": g.arena.myPower,
        "wood": g.resources.wood,
        "food": g.resources.food,
        "contentment": g.chief.contentment,
        "century_player_sync_at": g.century_player_sync_at,
    }


def building_level_rows(levels: dict[str, int | str]) -> list[dict[str, Any]]:
    reg = get_building_registry()
    rows: list[dict[str, Any]] = []
    for bid_raw, val in levels.items():
        bid = str(bid_raw).strip()
        if not bid:
            continue
        try:
            lv: int | str = int(str(val).strip())
        except ValueError:
            lv = str(val).strip() or "—"
        bdef = reg.by_id(bid)
        display = bdef.name if bdef else bid
        cat = bdef.category if bdef else "—"
        rows.append(
            {
                "id": bid,
                "building": display,
                "category": cat,
                "level": lv,
            }
        )
    rows.sort(key=lambda r: str(r["building"]).lower())
    return rows


def building_level_rows_from_redis(state: dict[str, str]) -> list[dict[str, Any]]:
    levels: dict[str, str] = {}
    for key, val in state.items():
        if not key.startswith(_LEVEL_PREFIX):
            continue
        bid = key[len(_LEVEL_PREFIX) :].strip()
        if bid:
            levels[bid] = val
    return building_level_rows(levels)  # ty: ignore[invalid-argument-type]


def _format_seen_at(ts: object) -> str:
    try:
        seen = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if seen <= 0:
        return "—"
    delta = max(0, int(time.time() - seen))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def hero_entries_rows(entries: dict[str, Any]) -> list[dict[str, Any]]:
    reg = get_hero_registry()
    rows: list[dict[str, Any]] = []
    for hid, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        hdef: HeroDef | None = reg.by_id(hid)
        name = str(raw.get("name") or (hdef.name if hdef else hid) or hid)
        try:
            level = int(raw.get("level"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            level = 0
        try:
            shards_cur = int(raw.get("shards_current"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            shards_cur = 0
        try:
            shards_req = int(raw.get("shards_required"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            shards_req = 0
        available = raw.get("available")
        available_bool = True if available is None else bool(available)
        seen_ts = raw.get("seen_at")
        if seen_ts in (None, 0, "0"):
            seen_ts = raw.get("last_seen_at")
        rows.append(
            {
                "id": hid,
                "hero": name,
                "available": available_bool,
                "level": level,
                "shards_current": shards_cur,
                "shards_required": shards_req,
                "red_dot": bool(raw.get("red_dot")),
                "upgrade": bool(raw.get("isUpgradeAvailable")),
                "rarity": (hdef.rarity if hdef else "") or "—",
                "class": (hdef.hero_class if hdef else "") or "—",
                "sub_class": (hdef.sub_class if hdef else "") or "—",
                "seen": _format_seen_at(seen_ts),
            }
        )
    return rows


def build_heroes_view(g: GamerState) -> dict[str, Any]:
    entries_raw = g.heroes.entries or {}
    entries: dict[str, Any] = {
        str(k): v for k, v in entries_raw.items() if isinstance(v, dict)
    }
    reg = get_hero_registry()
    rows_all = hero_entries_rows(entries)
    owned = sorted(
        (r for r in rows_all if r["available"]),
        key=lambda r: (-int(r["level"] or 0), str(r["hero"]).lower()),
    )
    locked = sorted(
        (r for r in rows_all if not r["available"]),
        key=lambda r: (
            -((int(r["shards_current"]) / int(r["shards_required"])) if int(r["shards_required"]) > 0 else 0),
            str(r["hero"]).lower(),
        ),
    )
    seen_ids = set(entries.keys())
    missing = [
        {
            "id": h.id,
            "hero": h.name,
            "rarity": h.rarity or "—",
            "class": h.hero_class or "—",
            "sub_class": h.sub_class or "—",
        }
        for h in reg.heroes
        if h.id not in seen_ids
    ]
    missing.sort(key=lambda r: str(r["hero"]).lower())
    return {
        "metrics": {
            "owned": sum(1 for r in rows_all if r["available"]),
            "locked": sum(1 for r in rows_all if not r["available"]),
            "registry_total": len(reg.heroes),
            "notify": bool(g.heroes.isnotify),
        },
        "owned": owned,
        "locked": locked,
        "missing": missing,
        "entries_raw": entries_raw,
    }


def build_persisted_player_view(g: GamerState) -> dict[str, Any]:
    yaml_levels = dict(g.buildings.levels)
    if g.buildings.furnace.level and "furnace" not in yaml_levels:
        yaml_levels["furnace"] = g.buildings.furnace.level
    b = g.buildings
    return {
        "player_id": str(g.id),
        "summary": gamer_summary_row(g),
        "gamer": g.model_dump(mode="json"),
        "building_levels": building_level_rows(yaml_levels),  # ty: ignore[invalid-argument-type]
        "buildings_hud": {
            "queue1": b.queue1 or "—",
            "queue2": b.queue2 or "—",
            "hud": b.state.text or "—",
        },
        "resources": {
            "wood": g.resources.wood,
            "food": g.resources.food,
            "iron": g.resources.iron,
            "meat": g.resources.meat,
            "silver_keys": g.resources.silver_keys,
            "gold_keys": g.resources.gold_keys,
            "diamond": g.resources.diamond,
        },
        "recruitment": {
            "free_recruitments_today": g.events.recruitment.free_recruitments_today,
        },
        "troops": {
            "infantry": g.troops.infantry.state.TextStatus or "—",
            "lancer": g.troops.lancer.state.TextStatus or "—",
            "marksman": g.troops.marksman.state.TextStatus or "—",
            "available_infantry": bool(g.troops.infantry.state.isAvailable),
            "available_lancer": bool(g.troops.lancer.state.isAvailable),
            "available_marksman": bool(g.troops.marksman.state.isAvailable),
        },
        "alliance_block": {
            "alliance": g.alliance.name or "—",
            "alliance_power": g.alliance.power,
            "members": f"{g.alliance.members.count}/{g.alliance.members.max}",
            "exploration_level": g.exploration.level,
            "exploration_power": g.exploration.state.myPower,
            "arena_rank": g.arena.rank,
            "arena_power": g.arena.myPower,
            "contentment": g.chief.contentment,
        },
        "heroes": build_heroes_view(g),
    }


def get_persisted_player(player_id: str) -> dict[str, Any]:
    db, parse_err, raw_json = load_state_db()
    rel = state_db_path().relative_to(repo_root()).as_posix()
    base = {
        "state_path": rel,
        "storage": "sqlite",
        "parse_error": parse_err,
        "raw_yaml": raw_json if parse_err else None,
        "raw_json": raw_json if parse_err else None,
    }
    if parse_err or db is None:
        return {**base, "player": None}
    target = str(player_id).strip()
    for g in db.gamers:
        if str(g.id) == target:
            return {**base, "player": build_persisted_player_view(g)}
    msg = f"player not in state DB: {player_id}"
    raise KeyError(msg)


def build_state_db_overview() -> dict[str, Any]:
    db, parse_err, _ = load_state_db()
    rel = state_db_path().relative_to(repo_root()).as_posix()
    gamers: list[dict[str, Any]] = []
    if db and not parse_err:
        gamers = [gamer_summary_row(g) for g in db.gamers]
    path = state_db_path()
    return {
        "state_path": rel,
        "storage": "sqlite",
        "parse_error": parse_err,
        "file_exists": path.is_file(),
        "gamers": gamers,
    }


def get_player_power_stats(player_id: str) -> dict[str, Any]:
    return get_player_stats(player_id)


def sync_player_from_century(player_id: str) -> dict[str, Any]:
    """Pull Century API data and persist to SQLite (+ best-effort devices.yaml)."""
    fid = int(str(player_id).strip())
    now = time.time()
    steps: list[dict[str, str]] = []

    try:
        data = asyncio.run(CenturyClient().fetch_player(fid))
    except CenturyAPIError as exc:
        return {"ok": False, "error": f"Century API error: {exc}", "steps": steps}
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Century sync failed: {type(exc).__name__}: {exc}",
            "steps": steps,
        }

    steps.append(
        {
            "step": "fetch",
            "detail": f"Got {data.nickname!r} · stove {data.stove_level} · KID {data.kid}",
        }
    )

    try:
        store = get_state_store().get_or_create(str(fid), nickname=data.nickname)
        store.update_from_flat(
            {
                "nickname": data.nickname,
                "kid": data.kid,
                "avatar": data.avatar_image or "",
                "buildings.furnace.level": data.stove_level,
                "buildings.furnace.power": data.stove_lv_content,
                "buildings.levels.furnace": int(data.stove_level),
                "century_player_sync_at": float(now),
            }
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"state DB persist failed: {type(exc).__name__}: {exc}",
            "steps": steps,
        }

    steps.append({"step": "persist", "detail": f"Updated {state_db_path().relative_to(repo_root())}"})

    devices_note = "skipped devices.yaml"
    try:
        iid = infer_instance_id_for_player(str(fid))
        if iid:
            upsert_device_gamer(
                path=repo_root() / "db" / "devices.yaml",
                device_name=iid,
                player_id=str(fid),
                nickname=data.nickname,
            )
            devices_note = f"Linked instance {iid!r} in devices.yaml"
    except Exception as exc:
        devices_note = f"devices.yaml upsert skipped: {type(exc).__name__}: {exc}"

    steps.append({"step": "devices", "detail": devices_note})

    return {
        "ok": True,
        "player_id": str(fid),
        "nickname": data.nickname,
        "stove_level": data.stove_level,
        "kid": data.kid,
        "steps": steps,
    }


def build_live_player_state(player_id: str, state: dict[str, str]) -> dict[str, Any]:
    nick = (state.get("nickname") or "").strip()
    stove = (state.get("stove_level") or "").strip()
    kid = (state.get("kid") or "").strip()
    avatar_url = (state.get("avatar_image") or "").strip()
    return {
        "player_id": player_id,
        "fields": dict(sorted(state.items())),
        "field_count": len(state),
        "nickname": nick,
        "stove_level": stove,
        "kid": kid,
        "avatar_image": avatar_url,
        "building_levels": building_level_rows_from_redis(state),
    }
