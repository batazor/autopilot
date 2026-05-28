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


def get_player_stats(player_id: str) -> dict[str, Any]:
    return get_player_power_stats(player_id)


def get_state_db_overview() -> dict[str, Any]:
    return build_state_db_overview()


def century_sync(player_id: str) -> dict[str, Any]:
    return sync_player_from_century(player_id)


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
