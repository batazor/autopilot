"""Lightweight manual scenario executor for IA Editor mode."""
from __future__ import annotations

import asyncio
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
_MANUAL_PREFIXES = ("manual:", "ui:")


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


def _is_manual_scenario_payload(data: dict[str, Any], *, repo_root: Any) -> bool:
    task_id = str(data.get("task_id") or "").strip()
    if not task_id.startswith(_MANUAL_PREFIXES):
        return False
    scenario_key = str(data.get("dsl_scenario") or data.get("task_type") or "").strip()
    return bool(scenario_key and template_resolver.load_doc(repo_root, scenario_key))


async def _pop_manual_item(
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
        if not _is_manual_scenario_payload(data, repo_root=repo_root):
            continue
        try:
            claimed = int(await redis.zrem(key, raw))
        except (TypeError, ValueError):
            claimed = 0
        if claimed != 1:
            continue
        return queue._build_queue_item(data, default_run_at=float(score))  # noqa: SLF001
    return None


async def _execute_item(
    redis: aioredis.Redis,  # type: ignore[type-arg]
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
    settings = load_settings()
    redis = aioredis.from_url(settings.redis.url, decode_responses=True, socket_connect_timeout=5.0)
    queue = RedisQueue(redis, settings)
    repo_root = default_repo_root()
    logger.info("IA manual scenario executor started for %d instance(s)", len(settings.instances))
    try:
        while True:
            did_work = False
            for inst in settings.instances:
                item = await _pop_manual_item(
                    redis,
                    queue,
                    instance_id=inst.instance_id,
                    repo_root=repo_root,
                )
                if item is None:
                    continue
                did_work = True
                await _execute_item(redis, item)
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
