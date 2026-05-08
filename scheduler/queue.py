from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import redis.asyncio as aioredis

from config.devices import player_ids_for_device
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
    # Optional overlay match threshold (when task_type == "overlay_tap")
    threshold: float | None = None
    # Optional overlay match score/confidence (when task_type == "overlay_tap")
    score: float | None = None
    # Optional "assume screen after tap" (overlay_tap only)
    set_node: str | None = None
    # Optional DSL scenario key (imperative_drafts runner).
    dsl_scenario: str | None = None


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
        threshold: float | None = None,
        score: float | None = None,
        set_node: str | None = None,
        dsl_scenario: str | None = None,
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
        if threshold is not None:
            body["threshold"] = float(threshold)
        if score is not None:
            fs = float(score)
            body["score"] = fs
            # Alias: some payloads/tools only preserved one key; pop_due prefers this first.
            body["overlay_match_score"] = fs
        if set_node is not None and str(set_node).strip() != "":
            body["set_node"] = str(set_node).strip()
        if dsl_scenario is not None and str(dsl_scenario).strip() != "":
            body["dsl_scenario"] = str(dsl_scenario).strip()
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

    @staticmethod
    @lru_cache(maxsize=1)
    def _task_types_requiring_node() -> set[str]:
        """Task types from `scenarios/by_cron/*.yaml` that declare `node`.

        These tasks must not run while instance `current_screen` is empty/unknown.
        """
        repo_root = Path(__file__).resolve().parent.parent
        cron_dir = repo_root / "scenarios" / "by_cron"
        if not cron_dir.is_dir():
            return set()
        try:
            import yaml
        except Exception:
            return set()

        out: set[str] = set()
        for yml in sorted(cron_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            if not str(raw.get("node") or "").strip():
                continue
            t = str(raw.get("task") or raw.get("task_type") or "").strip()
            if not t:
                t = yml.stem
            if t:
                out.add(t)
        return out

    async def pop_due(self, instance_id: str, *, current_screen: str = "") -> QueueItem | None:
        import json

        now = time.time()
        # Fetch earliest due tasks (score <= now) for players on this instance.
        # Also allow device-level items with empty player_id ("") which will be
        # resolved to an active player at execution time.
        instance_players = self._players_for_instance(instance_id)
        # Include players discovered at runtime (OCR'd in-game id written by who_i_am).
        try:
            raw_ap = await self._redis.hget(
                f"wos:instance:{instance_id}:state", "active_player"
            )
            ap = (raw_ap.decode() if isinstance(raw_ap, bytes) else str(raw_ap or "")).strip()
            if ap:
                instance_players = instance_players | {ap}
        except Exception:
            pass
        candidates = await self._redis.zrangebyscore(_QUEUE_KEY, "-inf", now)

        due: list[tuple[str, dict[str, object]]] = []
        for raw in candidates:
            data = json.loads(raw)
            pid = str(data.get("player_id", ""))
            if data["instance_id"] == instance_id and (pid == "" or pid in instance_players):
                due.append((raw, data))

        if not due:
            return None

        # Unknown/none: do not run tasks that were defined with a required `node` in specs.
        if not str(current_screen or "").strip():
            gated = self._task_types_requiring_node()
            if gated:
                due = [x for x in due if str(x[1].get("task_type") or "") not in gated]
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
        thr = data.get("threshold")
        threshold = float(thr) if thr is not None else None
        sc = data.get("overlay_match_score")
        if sc is None:
            sc = data.get("score")
        score = float(sc) if sc is not None else None
        sn = data.get("set_node")
        set_node = str(sn).strip() if sn is not None and str(sn).strip() != "" else None
        ds = data.get("dsl_scenario")
        dsl_scenario = str(ds).strip() if ds is not None and str(ds).strip() != "" else None
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
            threshold=threshold,
            score=score,
            set_node=set_node,
            dsl_scenario=dsl_scenario,
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
            thr = data.get("threshold")
            threshold = float(thr) if thr is not None else None
            sc = data.get("overlay_match_score")
            if sc is None:
                sc = data.get("score")
            score = float(sc) if sc is not None else None
            sn = data.get("set_node")
            set_node = str(sn).strip() if sn is not None and str(sn).strip() != "" else None
            ds = data.get("dsl_scenario")
            dsl_scenario = str(ds).strip() if ds is not None and str(ds).strip() != "" else None
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
                    threshold=threshold,
                    score=score,
                    set_node=set_node,
                    dsl_scenario=dsl_scenario,
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

    async def remove_by_task_type(self, task_type: str, instance_id: str) -> int:
        """Remove all queued items matching task_type + instance_id. Returns count removed."""
        import json

        all_items = await self._redis.zrangebyscore(_QUEUE_KEY, "-inf", "+inf")
        removed = 0
        for raw in all_items:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if (
                str(data.get("task_type") or "") == task_type
                and str(data.get("instance_id") or "") == instance_id
            ):
                await self._redis.zrem(_QUEUE_KEY, raw)
                removed += 1
        return removed

    def _players_for_instance(self, instance_id: str) -> set[str]:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return set(player_ids_for_device(inst.bluestacks_window_title))
        return set()
