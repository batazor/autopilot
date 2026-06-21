"""Module-scoped overlay analyzer loop for IA Editor mode."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import cv2
import redis.asyncio as aioredis

from analysis.overlay import run_overlay_analysis
from analysis.overlay_ttl_state import (
    persist_overlay_ttl_state_to_redis,
    sync_overlay_ttl_state_from_redis,
)
from config.loader import load_settings
from config.paths import repo_root as default_repo_root
from config.state_store import get_state_store
from dashboard.reference_preview import rolling_live_preview_path
from layout.area_manifest import load_area_doc
from scheduler.queue import RedisQueue
from services import get_bot_actions
from worker.instance_worker_overlay import InstanceWorkerOverlayMixin

logger = logging.getLogger(__name__)

_THREAD_NAME = "wos-ia-overlay-analyzer"
_DISABLED = "disabled"
_ANALYZE_INTERVAL_SECONDS = 1.0
_STATUS_TTL_SECONDS = 300
_EVENTS_MAX = 50


class _OverlayPusher(InstanceWorkerOverlayMixin):
    pass


def _make_overlay_pusher() -> _OverlayPusher:
    pusher = _OverlayPusher()
    pusher._bot_actions = get_bot_actions()
    return pusher


def analyzer_scope_key(instance_id: str) -> str:
    return f"wos:ui:ia_analyzer:scope:{str(instance_id or '').strip() or 'unknown'}"


def analyzer_status_key(instance_id: str) -> str:
    return f"wos:ui:ia_analyzer:last:{str(instance_id or '').strip() or 'unknown'}"


def analyzer_events_key(instance_id: str) -> str:
    return f"wos:ui:ia_analyzer:events:{str(instance_id or '').strip() or 'unknown'}"


def normalize_analyzer_scope(raw: object) -> str:
    scope = str(raw or "").strip()
    return scope or _DISABLED


def _existing_overlay_thread() -> threading.Thread | None:
    for thread in threading.enumerate():
        if thread.name == _THREAD_NAME and thread.is_alive():
            return thread
    return None


async def _read_state(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    instance_id: str,
) -> dict[str, str]:
    row = await redis.hgetall(f"wos:instance:{instance_id}:state")
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else str(v)
        )
        for k, v in row.items()
    }


def _state_flat(active_player: str | None) -> dict[str, Any] | None:
    player = str(active_player or "").strip()
    if not player:
        return None
    try:
        return get_state_store().get_or_create(player).to_flat_dict()
    except Exception:
        logger.debug("IA analyzer: state_flat lookup failed for player=%s", player, exc_info=True)
        return None


async def _queue_snapshot(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    instance_id: str,
) -> set[str]:
    raw_rows = await redis.zrange(f"wos:queue:{instance_id}", 0, -1)
    return {str(row.decode() if isinstance(row, bytes) else row) for row in raw_rows}


def _push_targets(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("pushScenario")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("type") or "").strip()
        if name:
            out.append(name)
    return out


async def _throttled_targets(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    *,
    instance_id: str,
    active_player: str | None,
    targets: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    player = str(active_player or "").strip()
    for target in targets:
        key = (
            f"wos:player:{player}:push_ttl:{target}"
            if player
            else f"wos:instance:{instance_id}:push_ttl:{target}"
        )
        ttl = await redis.ttl(key)
        if ttl and int(ttl) > 0:
            out.append({"scenario": target, "ttl": int(ttl), "key": key})
    return out


async def _write_status(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    instance_id: str,
    status: dict[str, Any],
) -> None:
    text = json.dumps(status, ensure_ascii=False, default=str)
    await redis.set(analyzer_status_key(instance_id), text, ex=_STATUS_TTL_SECONDS)
    if status.get("matched") or status.get("pushed") or status.get("throttled"):
        key = analyzer_events_key(instance_id)
        await redis.lpush(key, text)
        await redis.ltrim(key, 0, _EVENTS_MAX - 1)
        await redis.expire(key, _STATUS_TTL_SECONDS)


async def _analyze_instance(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    queue: RedisQueue,
    pusher: _OverlayPusher,
    *,
    instance_id: str,
    scope: str,
    rule_eval_state: dict[str, float],
) -> None:
    started = time.time()
    state = await _read_state(redis, instance_id)
    current_screen = str(state.get("current_screen") or "").strip()
    active_player = str(state.get("active_player") or "").strip()
    base_status: dict[str, Any] = {
        "at": started,
        "scope": scope,
        "current_screen": current_screen,
        "active_player": active_player,
    }
    # Keep scanning overlays even while a task is running so a page-specific
    # scenario (e.g. ``shop.dawn_market``) can outrank a lower-priority cycle
    # scenario (e.g. ``tabs.strip.advance``) and cooperatively preempt it via
    # ``_preempted_by_higher_priority`` (ADR 0001 §5). Per-rule ``ttl:`` debounce
    # + queue dedup index keep duplicate pushes off the queue.

    image_path = rolling_live_preview_path(instance_id)
    image = cv2.imread(str(image_path))
    if image is None:
        await _write_status(
            redis,
            instance_id,
            {**base_status, "skipped": "missing_preview"},
        )
        return

    await sync_overlay_ttl_state_from_redis(
        redis,
        instance_id=instance_id,
        player_id=active_player,
        rule_eval_state=rule_eval_state,
    )
    before = await _queue_snapshot(redis, instance_id)
    results = await run_overlay_analysis(
        image,
        repo_root=default_repo_root(),
        area_doc=load_area_doc(default_repo_root()),
        current_screen=current_screen or None,
        rule_eval_state=rule_eval_state,
        state_flat=_state_flat(active_player),
        module_scope=scope,
    )
    await persist_overlay_ttl_state_to_redis(
        redis,
        instance_id=instance_id,
        player_id=active_player,
        rule_eval_state=rule_eval_state,
    )
    matched: list[dict[str, Any]] = []
    throttled: list[dict[str, Any]] = []
    for name, payload in results.items():
        if not isinstance(payload, dict) or not payload.get("matched"):
            continue
        targets = _push_targets(payload)
        matched.append(
            {
                "rule": name,
                "region": str(payload.get("region") or ""),
                "action": str(payload.get("action") or ""),
                "pushScenario": targets,
            }
        )
        throttled.extend(
            await _throttled_targets(
                redis,
                instance_id=instance_id,
                active_player=active_player,
                targets=targets,
            )
        )

    pusher._cfg = SimpleNamespace(instance_id=instance_id)
    pusher._redis = redis
    pusher._queue = queue
    await pusher._schedule_overlay_matches(results, active_player=active_player or None)

    after = await _queue_snapshot(redis, instance_id)
    pushed: list[dict[str, Any]] = []
    for raw in sorted(after - before):
        with suppress(Exception):
            doc = json.loads(raw)
            pushed.append(
                {
                    "task_id": str(doc.get("task_id") or ""),
                    "task_type": str(doc.get("task_type") or ""),
                    "region": str(doc.get("region") or ""),
                }
            )

    await _write_status(
        redis,
        instance_id,
        {
            **base_status,
            "matched": matched,
            "pushed": pushed,
            "throttled": throttled,
            "duration_ms": int((time.time() - started) * 1000),
        },
    )


async def _overlay_loop() -> None:
    from config.redis_metrics import instrument_redis_client

    settings = load_settings()
    redis = aioredis.from_url(settings.redis.url, decode_responses=True, socket_connect_timeout=5.0)
    instrument_redis_client(redis, component="ia_overlay")
    queue = RedisQueue(redis, settings)
    pusher = _make_overlay_pusher()
    rule_eval_state_by_scope: dict[tuple[str, str], dict[str, float]] = {}
    logger.info("IA overlay analyzer started for %d instance(s)", len(settings.instances))
    try:
        while True:
            for inst in settings.instances:
                instance_id = inst.instance_id
                scope = normalize_analyzer_scope(await redis.get(analyzer_scope_key(instance_id)))
                if scope == _DISABLED:
                    await _write_status(
                        redis,
                        instance_id,
                        {"at": time.time(), "scope": scope, "skipped": "disabled"},
                    )
                    continue
                state_key = (instance_id, scope)
                rule_state = rule_eval_state_by_scope.setdefault(state_key, {})
                try:
                    await _analyze_instance(
                        redis,
                        queue,
                        pusher,
                        instance_id=instance_id,
                        scope=scope,
                        rule_eval_state=rule_state,
                    )
                except Exception:
                    logger.exception("IA overlay analyzer failed for %s", instance_id)
            await asyncio.sleep(_ANALYZE_INTERVAL_SECONDS)
    finally:
        await redis.aclose()


def ensure_ia_overlay_analyzer() -> None:
    """Start IA Editor's module-scoped overlay analyzer once per process."""

    if _existing_overlay_thread() is not None:
        return

    def _run() -> None:
        try:
            asyncio.run(_overlay_loop())
        except Exception:
            logger.exception("IA overlay analyzer crashed")

    thread = threading.Thread(target=_run, daemon=True, name=_THREAD_NAME)
    thread.start()
