"""DSL exec handlers for the main menu panel."""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from config.state_store import get_state_store
from dashboard.dashboard_events import publish_dashboard_event_throttled_async
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)
from tasks.dsl_scenario_helpers import _parse_hms_to_seconds

logger = logging.getLogger(__name__)

_TROOP_TYPES: tuple[str, ...] = ("infantry", "lancer", "marksman")
_MARCH_SLOT_COUNT = 6


async def _read_hash(redis_client: Any, key: str, field: str) -> str:
    raw = await redis_client.hget(key, field)
    return _decode_redis_raw(raw)


def _parse_remaining_seconds(raw_seconds: str, raw_text: str) -> int | None:
    try:
        seconds = int(str(raw_seconds or "").strip())
    except (TypeError, ValueError):
        seconds = -1
    if seconds >= 0:
        return seconds
    parsed = _parse_hms_to_seconds(raw_text)
    return int(parsed) if parsed is not None else None


def _row_seen(troop_type: str, status_text: str, timer_text: str) -> bool:
    haystack = f"{status_text} {timer_text}".lower()
    if troop_type == "marksman":
        return "marksman" in haystack or "mark" in haystack
    return troop_type in haystack


def _parse_marching_count(text: str) -> tuple[int, int]:
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], 0
    return 0, 0


def _is_idle_text(text: str) -> bool:
    return "idle" in (text or "").lower()


async def _exec_sync_main_menu_training_status(ctx: DslExecContext) -> None:
    """Persist visible troop-training timers from the main menu into player state."""
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_main_menu_training_status: no redis client")
        ctx.result.update({"reason": "no_redis_client"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_main_menu_training_status: empty player_id")
        ctx.result.update({"reason": "empty_player_id"})
        return

    redis_key = f"wos:player:{player_id}:state"
    now = time.time()
    updates: dict[str, object] = {}
    result: dict[str, object] = {}

    for troop_type in _TROOP_TYPES:
        prefix = f"main_menu.training.{troop_type}"
        raw_seconds = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.remaining_s")
        timer_text = await _read_hash(
            ctx.redis_client, redis_key, f"{prefix}.remaining_s_text"
        )
        status_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status")
        status_text_raw = await _read_hash(
            ctx.redis_client, redis_key, f"{prefix}.status_text"
        )
        status = status_text or status_text_raw
        remaining_s = _parse_remaining_seconds(raw_seconds, timer_text)
        seen = remaining_s is not None or _row_seen(troop_type, status, timer_text)
        if not seen:
            result[troop_type] = {"action": "skipped", "reason": "row_not_seen"}
            continue

        remaining = max(0, int(remaining_s or 0))
        is_available = remaining <= 0
        text_status = timer_text.strip() if remaining > 0 else ""
        ends_at = now + remaining if remaining > 0 else 0.0
        state_prefix = f"troops.{troop_type}.state"
        updates.update(
            {
                f"{state_prefix}.isAvailable": is_available,
                f"{state_prefix}.TextStatus": text_status,
                f"{state_prefix}.training_remaining_s": remaining,
                f"{state_prefix}.training_ends_at": ends_at,
                f"{state_prefix}.training_checked_at": now,
            }
        )
        result[troop_type] = {
            "available": is_available,
            "remaining_s": remaining,
            "text": text_status,
            "ends_at": ends_at,
        }

    if not updates:
        logger.warning(
            "dsl exec sync_main_menu_training_status: no troop rows recognized player=%s",
            player_id,
        )
        ctx.result.update({"reason": "no_rows_recognized", "troops": result})
        return

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(updates)
    except Exception:
        logger.exception(
            "dsl exec sync_main_menu_training_status: state persist failed player=%s",
            player_id,
        )
        ctx.result.update({"reason": "state_persist_failed", "troops": result})
        return

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_main_menu_training_status",
    )
    ctx.result.update({"action": "stored", "troops": result})
    logger.info(
        "dsl exec sync_main_menu_training_status: stored player=%s troops=%s",
        player_id,
        result,
    )


async def _exec_sync_main_menu_marching_status(ctx: DslExecContext) -> None:
    """Persist visible Wilderness march queue rows from the main menu."""
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_main_menu_marching_status: no redis client")
        ctx.result.update({"reason": "no_redis_client"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_main_menu_marching_status: empty player_id")
        ctx.result.update({"reason": "empty_player_id"})
        return

    redis_key = f"wos:player:{player_id}:state"
    now = time.time()
    count_text = await _read_hash(
        ctx.redis_client,
        redis_key,
        "main_menu.marching.count_text",
    )
    if not count_text:
        count_text = await _read_hash(ctx.redis_client, redis_key, "main_menu.marching.count")
    active_count, capacity = _parse_marching_count(count_text)

    slots: dict[str, object] = {}
    recognized = 0
    for slot_no in range(1, _MARCH_SLOT_COUNT + 1):
        prefix = f"main_menu.marching.slot.{slot_no}"
        title = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.title")
        title_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.title_text")
        status = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status")
        status_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status_text")
        raw_seconds = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.remaining_s")
        timer_text = await _read_hash(
            ctx.redis_client,
            redis_key,
            f"{prefix}.remaining_s_text",
        )

        label = (title or title_text or "").strip()
        row_text = (status or status_text or label or timer_text).strip()
        remaining_s = _parse_remaining_seconds(raw_seconds, timer_text)
        is_idle = _is_idle_text(row_text) and remaining_s is None
        is_active = bool(label) or remaining_s is not None
        if not (is_idle or is_active):
            continue

        recognized += 1
        remaining = max(0, int(remaining_s or 0))
        ends_at = now + remaining if remaining > 0 else 0.0
        slots[str(slot_no)] = {
            "slot": slot_no,
            "status": "idle" if is_idle else "marching",
            "label": "" if is_idle else label,
            "raw_text": row_text,
            "time_text": timer_text.strip(),
            "remaining_s": remaining,
            "ends_at": ends_at,
            "checked_at": now,
        }

    if recognized == 0:
        logger.warning(
            "dsl exec sync_main_menu_marching_status: no march rows recognized player=%s",
            player_id,
        )
        ctx.result.update({"reason": "no_rows_recognized"})
        return

    if capacity <= 0:
        capacity = _MARCH_SLOT_COUNT
    updates: dict[str, object] = {
        "marches.active_count": active_count,
        "marches.capacity": capacity,
        "marches.checked_at": now,
        "marches.slots": slots,
    }
    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(updates)
    except Exception:
        logger.exception(
            "dsl exec sync_main_menu_marching_status: state persist failed player=%s",
            player_id,
        )
        ctx.result.update({"reason": "state_persist_failed", "slots": slots})
        return

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_main_menu_marching_status",
    )
    ctx.result.update(
        {
            "action": "stored",
            "active_count": active_count,
            "capacity": capacity,
            "slots": slots,
        }
    )
    logger.info(
        "dsl exec sync_main_menu_marching_status: stored player=%s active=%d/%d slots=%s",
        player_id,
        active_count,
        capacity,
        slots,
    )


DSL_EXEC_HANDLERS = {
    "sync_main_menu_training_status": _exec_sync_main_menu_training_status,
    "sync_main_menu_marching_status": _exec_sync_main_menu_marching_status,
}
