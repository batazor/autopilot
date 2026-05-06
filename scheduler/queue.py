from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from config.loader import get_settings

logger = logging.getLogger(__name__)

_QUEUE_KEY = "wos:queue"


@dataclass(frozen=True)
class QueueItem:
    task_id: str
    player_id: str
    task_type: str
    priority: int
    run_at: float
    instance_id: str
    # Region name in area.json; bbox is resolved when the task runs.
    region: str | None = None
    # Optional tap override (% of framebuffer). Used when overlay matched inside ``search_region``.
    tap_x_pct: float | None = None
    tap_y_pct: float | None = None


class RedisQueue:
    def __init__(self, redis_client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client
        self._settings = get_settings()

    async def schedule(
        self,
        task_id: str,
        player_id: str,
        task_type: str,
        priority: int,
        run_at: float,
        instance_id: str,
        region: str | None = None,
        *,
        tap_x_pct: float | None = None,
        tap_y_pct: float | None = None,
        skip_if_duplicate: bool = False,
    ) -> bool:
        """Enqueue a task.

        Returns False if ``skip_if_duplicate`` and the same player/type/region is queued.
        """
        import json

        if skip_if_duplicate and await self.has_pending_duplicate(
            player_id=player_id, task_type=task_type, region=region
        ):
            logger.debug(
                "Skip duplicate queue item: player=%s type=%s region=%r",
                player_id,
                task_type,
                region,
            )
            return False

        body: dict[str, object] = {
            "task_id": task_id,
            "player_id": player_id,
            "task_type": task_type,
            "priority": priority,
            "run_at": run_at,
            "instance_id": instance_id,
        }
        if region is not None and str(region).strip() != "":
            body["region"] = str(region).strip()
        if tap_x_pct is not None:
            body["tap_x_pct"] = float(tap_x_pct)
        if tap_y_pct is not None:
            body["tap_y_pct"] = float(tap_y_pct)
        payload = json.dumps(body)
        # Score = run_at unix ts (earlier = higher priority in ZADD)
        await self._redis.zadd(_QUEUE_KEY, {payload: run_at})
        return True

    async def has_pending_duplicate(
        self,
        *,
        player_id: str,
        task_type: str,
        region: str | None,
    ) -> bool:
        """True if the queue already has an item with the same player, task_type, and region."""
        import json

        want_region = str(region).strip() if region else ""
        all_items = await self._redis.zrangebyscore(_QUEUE_KEY, "-inf", "+inf")
        for raw in all_items:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if str(data.get("player_id", "")) != player_id:
                continue
            if str(data.get("task_type", "")) != task_type:
                continue
            got = data.get("region")
            got_s = str(got).strip() if got is not None else ""
            if got_s == want_region:
                return True
        return False

    async def pop_due(self, instance_id: str) -> QueueItem | None:
        import json

        now = time.time()
        # Fetch earliest due tasks (score <= now) for players on this instance
        instance_players = self._players_for_instance(instance_id)
        candidates = await self._redis.zrangebyscore(_QUEUE_KEY, "-inf", now)

        due: list[tuple[str, dict[str, object]]] = []
        for raw in candidates:
            data = json.loads(raw)
            if data["instance_id"] == instance_id and data["player_id"] in instance_players:
                due.append((raw, data))

        if not due:
            return None

        due.sort(
            key=lambda item: (
                -int(item[1].get("priority", 0)),
                float(item[1].get("run_at", now)),
            )
        )
        raw, data = due[0]
        await self._redis.zrem(_QUEUE_KEY, raw)
        reg = data.get("region")
        region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
        tap_x = data.get("tap_x_pct")
        tap_y = data.get("tap_y_pct")
        tap_x_pct = float(tap_x) if tap_x is not None else None
        tap_y_pct = float(tap_y) if tap_y is not None else None
        return QueueItem(
            task_id=data["task_id"],  # type: ignore[arg-type]
            player_id=data["player_id"],  # type: ignore[arg-type]
            task_type=data["task_type"],  # type: ignore[arg-type]
            priority=int(data.get("priority", 0)),  # type: ignore[arg-type]
            run_at=float(data.get("run_at", now)),  # type: ignore[arg-type]
            instance_id=data["instance_id"],  # type: ignore[arg-type]
            region=region,
            tap_x_pct=tap_x_pct,
            tap_y_pct=tap_y_pct,
        )

    async def peek_all(self) -> list[QueueItem]:
        import json

        items = await self._redis.zrangebyscore(_QUEUE_KEY, "-inf", "+inf", withscores=True)
        results: list[QueueItem] = []
        for raw, score in items:
            data = json.loads(raw)
            reg = data.get("region")
            region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
            tap_x = data.get("tap_x_pct")
            tap_y = data.get("tap_y_pct")
            tap_x_pct = float(tap_x) if tap_x is not None else None
            tap_y_pct = float(tap_y) if tap_y is not None else None
            results.append(
                QueueItem(
                    task_id=data["task_id"],
                    player_id=data["player_id"],
                    task_type=data["task_type"],
                    priority=data.get("priority", 0),
                    run_at=float(data.get("run_at", score)),
                    instance_id=data["instance_id"],
                    region=region,
                    tap_x_pct=tap_x_pct,
                    tap_y_pct=tap_y_pct,
                )
            )
        return results

    async def remove(self, task_id: str) -> None:
        import json

        all_items = await self._redis.zrangebyscore(_QUEUE_KEY, "-inf", "+inf")
        for raw in all_items:
            data = json.loads(raw)
            if data["task_id"] == task_id:
                await self._redis.zrem(_QUEUE_KEY, raw)
                return

    def _players_for_instance(self, instance_id: str) -> set[str]:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return set(inst.player_ids)
        return set()
