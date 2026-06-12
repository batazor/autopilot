"""Player state API helpers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from config.devices import invalidate_device_registry
from config.devices_db import delete_device_gamer
from config.state_sqlite import delete_player_state
from dashboard.player_state_data import (
    build_live_player_state,
    build_state_db_overview,
    get_persisted_player,
    get_player_power_stats,
    list_known_player_ids,
    sync_player_from_century,
)
from dashboard.redis_client import delete_player_redis, get_player_state_hash
from dashboard.reference_preview import load_rolling_instance_preview
from services.player_avatar_identity import (
    avatar_reference_meta,
    decode_png_bgr,
    save_avatar_reference_from_frame,
)


def list_player_ids(*, instance_id: str | None = None) -> list[str]:
    """All known player ids, or just those registered on ``instance_id``.

    When ``instance_id`` matches a device in the registry, only that device's
    profile-bound players are returned; if it doesn't match (or is empty),
    the global list is returned unchanged.
    """
    if not instance_id:
        return list_known_player_ids()
    try:
        from config.devices_db import load_registry
    except ImportError:
        return list_known_player_ids()
    registry = load_registry()
    bound = registry.player_ids_for_device(instance_id)
    if not bound:
        # Unknown device or no registered profiles — fall back to the global
        # list rather than returning nothing, so the UI keeps a working set.
        return list_known_player_ids()
    return bound


def build_player_state(client: redis.Redis, player_id: str) -> dict[str, Any]:
    state = get_player_state_hash(client, player_id)
    return build_live_player_state(player_id, state)


def get_persisted_state(player_id: str) -> dict[str, Any]:
    return get_persisted_player(player_id)


def get_tree_progress(player_id: str) -> dict[str, Any]:
    """Per-tech research levels + per-building levels for the trees overlay."""
    from config.state_store import get_state_store

    store = get_state_store().get(str(player_id))
    if store is None:
        msg = f"unknown player: {player_id}"
        raise KeyError(msg)
    snap = store.snapshot()
    return {
        "player_id": str(player_id),
        "research": dict(snap.researches.levels),
        "buildings": dict(snap.buildings.levels),
    }


def set_tree_progress(
    player_id: str,
    research: dict[str, int] | None = None,
    buildings: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Merge research/building level entries into the player's persisted state."""
    from config.state_store import get_state_store

    store = get_state_store().get(str(player_id))
    if store is None:
        msg = f"unknown player: {player_id}"
        raise KeyError(msg)
    for tech, lvl in (research or {}).items():
        store.set(f"researches.levels.{tech}", max(0, int(lvl)))
    for bid, lvl in (buildings or {}).items():
        store.set(f"buildings.levels.{bid}", max(0, int(lvl)))
    return get_tree_progress(player_id)


def get_player_stats(player_id: str) -> dict[str, Any]:
    return get_player_power_stats(player_id)


def get_state_db_overview() -> dict[str, Any]:
    return build_state_db_overview()


def century_sync(player_id: str) -> dict[str, Any]:
    return sync_player_from_century(player_id)


def avatar_reference_status(player_id: str) -> dict[str, Any]:
    meta = avatar_reference_meta(player_id)
    return {
        "player_id": meta.player_id,
        "exists": meta.exists,
        "reference": meta.rel_path,
        "mtime": meta.mtime,
    }


def update_avatar_reference(
    player_id: str,
    *,
    instance_id: str,
) -> dict[str, Any]:
    iid = (instance_id or "").strip()
    if not iid:
        msg = "instance_id is required"
        raise ValueError(msg)
    png, preview_rel, preview_mtime = load_rolling_instance_preview(iid)
    if png is None:
        msg = f"no rolling preview image available for {iid!r}"
        raise FileNotFoundError(msg)
    image_bgr = decode_png_bgr(png)
    out = save_avatar_reference_from_frame(player_id, image_bgr)
    return {
        **out,
        "instance_id": iid,
        "source_preview": preview_rel,
        "source_preview_mtime": preview_mtime,
    }


def delete_player(client: redis.Redis, player_id: str) -> dict[str, Any]:
    sqlite_counts = delete_player_state(player_id)
    device_rows_deleted = delete_device_gamer(player_id)
    if device_rows_deleted:
        invalidate_device_registry()
    redis_deleted = delete_player_redis(client, player_id)
    return {
        "ok": True,
        "player_id": str(player_id),
        "sqlite": sqlite_counts,
        "device_rows_deleted": device_rows_deleted,
        "redis_keys_deleted": redis_deleted,
    }


def suggest_active_player_id(client: redis.Redis, instance_id: str) -> str:
    from dashboard.redis_client import get_instance_state

    if not instance_id.strip():
        return ""
    state = get_instance_state(client, instance_id.strip())
    return (state.get("active_player") or "").strip()
