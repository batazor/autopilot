"""Lightweight manual scenario executor for IA Editor mode."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import threading
import time
from contextlib import suppress
from typing import Any

import redis.asyncio as aioredis

from config.loader import load_settings
from config.paths import repo_root as default_repo_root
from scenarios import template_resolver
from scheduler.queue import RedisQueue
from tasks.dsl_scenario import DslScenarioTask

logger = logging.getLogger(__name__)

_THREAD_NAME = "wos-ia-manual-scenario-executor"
_IA_SCENARIO_PREFIXES = ("manual:", "ui:", "ovl:")


def _existing_executor_thread() -> threading.Thread | None:
    for thread in threading.enumerate():
        if thread.name == _THREAD_NAME and thread.is_alive():
            return thread
    return None


def _queue_key(instance_id: str) -> str:
    return f"wos:queue:{str(instance_id or '').strip() or 'unknown'}"


def _running_key(instance_id: str) -> str:
    return f"wos:queue:running:{str(instance_id or '').strip() or 'unknown'}"


def _history_key(instance_id: str) -> str:
    return f"wos:queue:history:{str(instance_id or '').strip() or 'unknown'}"


def _is_ia_scenario_payload(data: dict[str, Any], *, repo_root: Any) -> bool:
    task_id = str(data.get("task_id") or "").strip()
    if not task_id.startswith(_IA_SCENARIO_PREFIXES):
        return False
    scenario_key = str(data.get("dsl_scenario") or data.get("task_type") or "").strip()
    return bool(scenario_key and template_resolver.load_doc(repo_root, scenario_key))


async def _resolve_overlay_player(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    item: Any,
) -> Any:
    """Resolve overlay-pushed device-level DSL items to the current active player."""

    if str(getattr(item, "player_id", "") or "").strip():
        return item
    task_id = str(getattr(item, "task_id", "") or "")
    if not task_id.startswith("ovl:"):
        return item
    instance_id = str(getattr(item, "instance_id", "") or "").strip()
    if not instance_id:
        return item
    active = ""
    with suppress(Exception):
        raw = await redis.hget(f"wos:instance:{instance_id}:state", "active_player")
        active = (raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip()
    if not active:
        return item
    return dataclasses.replace(item, player_id=active)


async def _pop_ia_item(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    queue: RedisQueue,
    *,
    instance_id: str,
    repo_root: Any,
) -> Any | None:
    key = _queue_key(instance_id)
    now = time.time()
    try:
        candidates = await redis.zrangebyscore(key, "-inf", now, withscores=True)
    except Exception:
        logger.debug("IA executor: failed reading queue for %s", instance_id, exc_info=True)
        return None

    for raw, score in candidates:
        text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if str(data.get("instance_id") or "") != instance_id:
            continue
        if not _is_ia_scenario_payload(data, repo_root=repo_root):
            continue
        try:
            claimed = int(await redis.zrem(key, raw))
        except (TypeError, ValueError):
            claimed = 0
        if claimed != 1:
            continue
        item = queue._build_queue_item(data, default_run_at=float(score))
        return await _resolve_overlay_player(redis, item)
    return None


async def _execute_item(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    queue: RedisQueue,
    item: Any,
) -> None:
    instance_id = str(item.instance_id or "").strip()
    scenario_key = str(item.dsl_scenario or item.task_type or "").strip()
    state_key = f"wos:instance:{instance_id}:state"
    started_at = time.time()
    running_payload = json.dumps(
        {
            "task_id": item.task_id,
            "task_type": item.task_type,
            "player_id": item.player_id,
            "priority": item.priority,
            "instance_id": instance_id,
            "region": item.region or "",
            "started_at": started_at,
            "ia_editor": True,
        },
        ensure_ascii=False,
    )
    await redis.set(_running_key(instance_id), running_payload, ex=180)
    await redis.hset(
        state_key,
        mapping={
            "state": "busy",
            "current_task_id": item.task_id,
            "current_task_type": item.task_type,
            "current_task_player": item.player_id,
            "current_task_started_at": str(started_at),
            "current_task_region": item.region or "",
            "current_scenario": scenario_key,
            "queue_blocked_reason": "",
            "last_error": "",
        },
    )

    error = ""
    result = None
    try:
        task = DslScenarioTask(
            task_id=item.task_id,
            player_id=item.player_id,
            priority=int(item.priority or 0),
            effective_priority=int(item.effective_priority or item.priority or 0),
            scenario_key=scenario_key,
            start_step_index=int(item.start_step_index or 0),
            redis_client=redis,
        )
        result = await task.execute(instance_id)
        logger.info(
            "IA executor: task done instance=%s task=%s success=%s",
            instance_id,
            item.task_id,
            getattr(result, "success", None),
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc!s}"
        logger.exception("IA executor: task failed instance=%s task=%s", instance_id, item.task_id)
    finally:
        finished_at = time.time()
        metadata = dict(getattr(result, "metadata", None) or {})
        history = {
            "task_id": item.task_id,
            "task_type": item.task_type,
            "player_id": item.player_id,
            "priority": item.priority,
            "instance_id": instance_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "success": bool(getattr(result, "success", False)) if result is not None else False,
            "error": error,
            "metadata": metadata,
            "ia_editor": True,
        }
        with suppress(Exception):
            await redis.lpush(_history_key(instance_id), json.dumps(history, ensure_ascii=False))
            await redis.ltrim(_history_key(instance_id), 0, 99)
        if result is not None and result.next_run_at is not None:
            metadata = dict(getattr(result, "metadata", None) or {})
            try:
                resume_step = int(metadata.get("resume_from_step_index") or 0)
            except (TypeError, ValueError):
                resume_step = 0
            with suppress(Exception):
                await queue.schedule(
                    task_id=item.task_id,
                    player_id=item.player_id,
                    task_type=item.task_type,
                    priority=item.priority,
                    run_at=result.next_run_at.timestamp(),
                    instance_id=instance_id,
                    region=item.region,
                    tap_x_pct=item.tap_x_pct,
                    tap_y_pct=item.tap_y_pct,
                    threshold=item.threshold,
                    score=item.score,
                    match_top_left_x=item.match_top_left_x,
                    match_top_left_y=item.match_top_left_y,
                    template_w=item.template_w,
                    template_h=item.template_h,
                    tap_match_x_pct=item.tap_match_x_pct,
                    tap_match_y_pct=item.tap_match_y_pct,
                    dsl_scenario=item.dsl_scenario,
                    start_step_index=max(0, resume_step),
                    skip_if_duplicate=False,
                    dedup_ignore_region=True,
                )
        with suppress(Exception):
            await redis.delete(_running_key(instance_id))
        await redis.hset(
            state_key,
            mapping={
                "state": "ready",
                "current_task_id": "",
                "current_task_type": "",
                "current_task_player": "",
                "current_task_region": "",
                "current_scenario": "",
                "last_error": error,
            },
        )


async def _executor_loop() -> None:
    from config.redis_metrics import instrument_redis_client

    settings = load_settings()
    redis = aioredis.from_url(settings.redis.url, decode_responses=True, socket_connect_timeout=5.0)
    instrument_redis_client(redis, component="ia_queue")
    queue = RedisQueue(redis, settings)
    repo_root = default_repo_root()
    logger.info("IA manual scenario executor started for %d instance(s)", len(settings.instances))
    try:
        while True:
            did_work = False
            for inst in settings.instances:
                item = await _pop_ia_item(
                    redis,
                    queue,
                    instance_id=inst.instance_id,
                    repo_root=repo_root,
                )
                if item is None:
                    continue
                did_work = True
                await _execute_item(redis, queue, item)
            await asyncio.sleep(0.2 if did_work else 0.8)
    finally:
        await redis.aclose()


def ensure_ia_queue_executor() -> None:
    """Start IA Editor's manual scenario executor once per Streamlit process."""

    if os.environ.get("WOS_IA_DISABLE_QUEUE_EXECUTOR", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    if _existing_executor_thread() is not None:
        return

    def _run() -> None:
        try:
            asyncio.run(_executor_loop())
        except Exception:
            logger.exception("IA manual scenario executor crashed")

    thread = threading.Thread(target=_run, daemon=True, name=_THREAD_NAME)
    thread.start()
