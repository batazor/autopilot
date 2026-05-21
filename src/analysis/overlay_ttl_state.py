"""Redis-backed overlay rule evaluation TTL state."""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Worker overlay tick: re-read full TTL hash when rev changes, or at this interval
# (covers redis-cli HDEL without a rev bump).
OVERLAY_TTL_FORCE_SYNC_INTERVAL_S = 30.0
# Mirror in-memory TTL to Redis at most this often (wiki "last eval" UI).
OVERLAY_TTL_PERSIST_INTERVAL_S = 5.0


def overlay_ttl_key(*, instance_id: str, player_id: str | None) -> str:
    player = str(player_id or "").strip()
    if player:
        return f"wos:player:{player}:overlay_ttl"
    return f"wos:instance:{str(instance_id or '').strip()}:overlay_ttl_anon"


def overlay_ttl_rev_key(*, instance_id: str, player_id: str | None) -> str:
    """Revision counter; bump to force workers to reload overlay TTL from Redis."""
    return f"{overlay_ttl_key(instance_id=instance_id, player_id=player_id)}:rev"


def _decode_mapping(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        ks = k.decode() if isinstance(k, bytes) else str(k)
        vs = v.decode() if isinstance(v, bytes) else str(v)
        out[ks] = vs
    return out


async def read_overlay_ttl_rev(
    redis_client: Any,
    *,
    instance_id: str,
    player_id: str | None,
) -> str:
    key = overlay_ttl_rev_key(instance_id=instance_id, player_id=player_id)
    try:
        raw = await redis_client.get(key)
    except Exception:
        logger.debug("overlay TTL rev read failed key=%s", key, exc_info=True)
        return "0"
    if raw is None:
        return "0"
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def bump_overlay_ttl_rev(
    redis_client: Any,
    *,
    instance_id: str,
    player_id: str | None,
) -> str:
    """Invalidate in-process overlay TTL caches (e.g. after wiki/redis HDEL)."""
    key = overlay_ttl_rev_key(instance_id=instance_id, player_id=player_id)
    try:
        n = await redis_client.incr(key)
    except Exception:
        logger.debug("overlay TTL rev bump failed key=%s", key, exc_info=True)
        return "0"
    return str(int(n))


async def sync_overlay_ttl_state_if_needed(
    redis_client: Any,
    *,
    instance_id: str,
    player_id: str | None,
    rule_eval_state: dict[str, float],
    cached_rev: str,
    last_sync_mono: float,
    force_interval_s: float = OVERLAY_TTL_FORCE_SYNC_INTERVAL_S,
) -> tuple[str, float]:
    """Reload TTL snapshot from Redis only when ``:rev`` changes or interval elapsed."""
    rev = await read_overlay_ttl_rev(
        redis_client, instance_id=instance_id, player_id=player_id
    )
    now_mono = time.monotonic()
    stale = rev != cached_rev
    due = (now_mono - last_sync_mono) >= force_interval_s
    if stale or due:
        await sync_overlay_ttl_state_from_redis(
            redis_client,
            instance_id=instance_id,
            player_id=player_id,
            rule_eval_state=rule_eval_state,
        )
        return rev, now_mono
    return cached_rev, last_sync_mono


async def maybe_persist_overlay_ttl_state_to_redis(
    redis_client: Any,
    *,
    instance_id: str,
    player_id: str | None,
    rule_eval_state: dict[str, float],
    last_persist_mono: float | None,
    min_interval_s: float = OVERLAY_TTL_PERSIST_INTERVAL_S,
) -> float | None:
    """Write TTL snapshot at most every ``min_interval_s`` seconds."""
    if not rule_eval_state:
        return last_persist_mono
    now_mono = time.monotonic()
    if (
        last_persist_mono is not None
        and (now_mono - last_persist_mono) < min_interval_s
    ):
        return last_persist_mono
    await persist_overlay_ttl_state_to_redis(
        redis_client,
        instance_id=instance_id,
        player_id=player_id,
        rule_eval_state=rule_eval_state,
    )
    return now_mono


async def sync_overlay_ttl_state_from_redis(
    redis_client: Any,
    *,
    instance_id: str,
    player_id: str | None,
    rule_eval_state: dict[str, float],
) -> None:
    """Replace in-memory monotonic TTL state from Redis wall-clock snapshot.

    This makes Redis the authoritative reset surface: deleting a field from
    ``wos:*:overlay_ttl*`` clears the corresponding in-process throttle on the
    next analyzer tick without restarting the worker.
    """

    key = overlay_ttl_key(instance_id=instance_id, player_id=player_id)
    try:
        raw = await redis_client.hgetall(key)
    except Exception:
        logger.debug("overlay TTL snapshot read failed key=%s", key, exc_info=True)
        return

    now_wall = time.time()
    now_mono = time.monotonic()
    synced: dict[str, float] = {}
    for rule_name, wall_s in _decode_mapping(raw).items():
        try:
            wall_ts = float(wall_s)
        except (TypeError, ValueError):
            continue
        synced[str(rule_name)] = now_mono - max(0.0, now_wall - wall_ts)

    rule_eval_state.clear()
    rule_eval_state.update(synced)


async def persist_overlay_ttl_state_to_redis(
    redis_client: Any,
    *,
    instance_id: str,
    player_id: str | None,
    rule_eval_state: dict[str, float],
) -> None:
    if not rule_eval_state:
        return
    now_wall = time.time()
    now_mono = time.monotonic()
    mapping: dict[str, str] = {}
    for rule_name, mono_ts in rule_eval_state.items():
        try:
            wall_ts = now_wall - (now_mono - float(mono_ts))
        except (TypeError, ValueError):
            continue
        mapping[str(rule_name)] = f"{wall_ts:.3f}"
    if not mapping:
        return

    key = overlay_ttl_key(instance_id=instance_id, player_id=player_id)
    try:
        await redis_client.hset(key, mapping=mapping)
    except Exception:
        logger.debug("overlay TTL snapshot write failed key=%s", key, exc_info=True)
