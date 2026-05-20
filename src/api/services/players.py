"""Player state API helpers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from ui.player_state_data import (
    build_live_player_state,
    build_state_db_overview,
    get_persisted_player,
    list_known_player_ids,
    sync_player_from_century,
)
from ui.redis_client import get_player_state_hash


def list_player_ids() -> list[str]:
    return list_known_player_ids()


def build_player_state(client: redis.Redis, player_id: str) -> dict[str, Any]:
    state = get_player_state_hash(client, player_id)
    return build_live_player_state(player_id, state)


def get_persisted_state(player_id: str) -> dict[str, Any]:
    return get_persisted_player(player_id)


def get_state_db_overview() -> dict[str, Any]:
    return build_state_db_overview()


def century_sync(player_id: str) -> dict[str, Any]:
    return sync_player_from_century(player_id)


def suggest_active_player_id(client: redis.Redis, instance_id: str) -> str:
    from ui.redis_client import get_instance_state

    if not instance_id.strip():
        return ""
    state = get_instance_state(client, instance_id.strip())
    return (state.get("active_player") or "").strip()
