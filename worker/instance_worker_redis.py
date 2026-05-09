from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any

import redis.asyncio as aioredis

from config.loader import get_settings
from fsm.machine import PlayerFSM
from fsm.states import InstanceState
from scheduler.claims import CooperativeClaims
from scheduler.queue import QueueItem, RedisQueue

logger = logging.getLogger(__name__)

_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"


class InstanceWorkerRedisMixin:
    _cfg: Any
    _redis: aioredis.Redis | None
    _queue: RedisQueue | None
    _claims: Any
    _player_fsms: dict[str, PlayerFSM]
    _instance_state: InstanceState
    _task_registry: dict[str, type]

    async def _connect(self) -> None:
        settings = get_settings()
        self._redis = aioredis.from_url(settings.redis.url)
        self._queue = RedisQueue(self._redis)
        self._claims = CooperativeClaims(self._redis)
        loop = asyncio.get_running_loop()
        # Import via worker.instance_worker so tests can monkeypatch it there and
        # to avoid relying on a direct import in this module.
        from worker import instance_worker

        for player_id in instance_worker.player_ids_for_device(self._cfg.bluestacks_window_title):
            fsm = PlayerFSM(player_id, self._redis, loop=loop)
            await fsm.restore_from_redis()
            self._player_fsms[player_id] = fsm

        inst_key = _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id)
        await self._redis.hset(
            inst_key,
            mapping={
                "state": InstanceState.READY,
                "active_player": "",
                "paused": "0",
                "worker_started_at": str(time.time()),
                "last_seen_at": str(time.time()),
                "last_error": "",
                "nav_error": "",
                "current_task_player": "",
                "current_task_started_at": "",
                "current_task_region": "",
                "current_task_threshold": "",
                "current_task_score": "",
                "last_overlay_match_threshold": "",
                "last_overlay_match_score": "",
                "last_overlay_match_region": "",
                "current_screen": "",
                "current_scenario": "",
            },
        )

    async def _disconnect_redis(self) -> None:
        """Drain async Redis connections before the supervisor event loop stops."""
        client = self._redis
        self._redis = None
        self._queue = None
        self._claims = None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:
            logger.debug(
                "Redis aclose failed for instance %s",
                self._cfg.instance_id,
                exc_info=True,
            )

    async def _set_instance_state(self, state: InstanceState, *, error: str = "") -> None:
        """Persist instance state to Redis for UI/debugging."""
        self._instance_state = state
        if self._redis is None:
            return
        mapping: dict[str, str] = {"state": str(state)}
        if error:
            mapping["last_error"] = error[:500]
        else:
            mapping["last_error"] = ""
        try:
            await self._redis.hset(
                _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
                mapping=mapping,
            )
        except Exception:
            logger.debug("Failed to persist instance state to Redis", exc_info=True)

    async def _pop_next_task(self) -> QueueItem | None:
        assert self._queue is not None
        current_screen = ""
        inst_key = _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id)
        if self._redis is not None:
            raw = await self._redis.hget(
                inst_key,
                "current_screen",
            )
            if raw is not None:
                current_screen = raw.decode() if isinstance(raw, bytes) else str(raw)
                current_screen = current_screen.strip()
        item = await self._queue.pop_due(
            self._cfg.instance_id,
            current_screen=current_screen,
        )
        if item is not None or self._redis is None:
            if item is not None:
                with suppress(Exception):
                    await self._redis.hset(inst_key, "queue_blocked_reason", "")
            return item

        reason = await self._queue_blocked_reason(current_screen=current_screen)
        with suppress(Exception):
            await self._redis.hset(inst_key, "queue_blocked_reason", reason)
        return None

    async def _queue_blocked_reason(self, *, current_screen: str) -> str:
        if self._redis is None:
            return ""
        inst = self._cfg.instance_id
        qkey = f"wos:queue:{inst}"
        try:
            rows = await self._redis.zrangebyscore(qkey, "-inf", time.time())
        except Exception:
            logger.debug("queue blocked reason: zrange failed", exc_info=True)
            return ""
        if not rows:
            return ""
        active_raw = await self._redis.hget(
            _INST_STATE_KEY_FMT.format(instance_id=inst),
            "active_player",
        )
        active = (
            active_raw.decode() if isinstance(active_raw, bytes) else str(active_raw or "")
        ).strip()
        if not active:
            return f"{len(rows)} due item(s) blocked: active_player is empty"
        if not str(current_screen or "").strip():
            return f"{len(rows)} due item(s) blocked: current_screen is empty"
        sample: list[str] = []
        for raw in rows[:3]:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            sample.append(
                f"{str(data.get('task_type') or '?')}[{str(data.get('player_id') or 'device')}]"
            )
        detail = ", ".join(sample)
        return f"{len(rows)} due item(s) not runnable for this instance/player: {detail}"

    async def _resolve_queue_item_player(self, item: QueueItem) -> QueueItem:
        """Resolve device-level queue items (player_id="") to an actual player id."""
        if item.player_id:
            return item

        active = None
        if self._redis is not None:
            raw = await self._redis.hget(
                _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id), "active_player"
            )
            if raw:
                active = (raw.decode() if isinstance(raw, bytes) else str(raw)).strip()

        # Non-DSL tasks must run under a player id; DSL tasks may be device-level.
        # Some tests construct InstanceWorker via object.__new__ (no __init__), so
        # _task_registry may be missing; fall back to the module-level registry.
        registry = getattr(self, "_task_registry", None)
        if not isinstance(registry, dict):
            from worker.instance_worker import _TASK_REGISTRY  # local import to avoid cycle

            registry = _TASK_REGISTRY

        if registry.get(item.task_type) is None:
            if not active:
                return item
            resolved = active
        else:
            # Import via worker.instance_worker so tests can monkeypatch it there.
            from worker import instance_worker

            _cfg_pids = instance_worker.player_ids_for_device(self._cfg.bluestacks_window_title)
            resolved = (active or (_cfg_pids[0] if _cfg_pids else "")).strip()
            if not resolved:
                return item

        return QueueItem(
            task_id=item.task_id,
            player_id=resolved,
            task_type=item.task_type,
            priority=item.priority,
            run_at=item.run_at,
            instance_id=item.instance_id,
            region=item.region,
            tap_x_pct=item.tap_x_pct,
            tap_y_pct=item.tap_y_pct,
            threshold=item.threshold,
            score=item.score,
            set_node=item.set_node,
            dsl_scenario=item.dsl_scenario,
            match_top_left_x=item.match_top_left_x,
            match_top_left_y=item.match_top_left_y,
            template_w=item.template_w,
            template_h=item.template_h,
            tap_match_x_pct=item.tap_match_x_pct,
            tap_match_y_pct=item.tap_match_y_pct,
            start_step_index=item.start_step_index,
        )

    async def _ensure_account(self, player_id: str) -> None:
        if self._redis is not None:
            await self._redis.hset(
                _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
                "active_player",
                player_id,
            )
