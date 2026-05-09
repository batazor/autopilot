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

def _queue_key(instance_id: str) -> str:
    iid = str(instance_id or "").strip()
    return f"wos:queue:{iid}" if iid else "wos:queue:unknown"


def _dup_region_key(region: str | None) -> str:
    return str(region).strip() if region else ""


def _dup_index_key(*, instance_id: str, player_id: str, task_type: str, region: str | None) -> str:
    iid = str(instance_id or "").strip() or "unknown"
    reg = _dup_region_key(region)
    pid = str(player_id or "").strip()
    return f"wos:queue:idx:{iid}:{task_type}:{reg}:{pid}"


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
    # Optional template match box for UI/debug (overlay-derived tasks).
    match_top_left_x: int | None = None
    match_top_left_y: int | None = None
    template_w: int | None = None
    template_h: int | None = None
    tap_match_x_pct: float | None = None
    tap_match_y_pct: float | None = None
    # Step index to resume from when re-enqueuing after a hand-pointer interruption.
    start_step_index: int = 0


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
        match_top_left_x: int | None = None,
        match_top_left_y: int | None = None,
        template_w: int | None = None,
        template_h: int | None = None,
        tap_match_x_pct: float | None = None,
        tap_match_y_pct: float | None = None,
        start_step_index: int = 0,
        skip_if_duplicate: bool = False,
        dedup_ignore_region: bool = False,
    ) -> bool:
        """Enqueue a task.

        Returns False if ``skip_if_duplicate`` and a matching item is already queued.

        By default, the duplicate signature includes ``region``.
        Set ``dedup_ignore_region=True`` to deduplicate by (instance_id, player_id, task_type)
        while still preserving ``region`` in the payload for UI/debugging.
        """
        import json

        if skip_if_duplicate and await self.has_pending_duplicate(
            player_id=player_id,
            task_type=task_type,
            region=region,
            instance_id=instance_id,
            ignore_region=dedup_ignore_region,
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
        if match_top_left_x is not None:
            body["match_top_left_x"] = int(match_top_left_x)
        if match_top_left_y is not None:
            body["match_top_left_y"] = int(match_top_left_y)
        if template_w is not None:
            body["template_w"] = int(template_w)
        if template_h is not None:
            body["template_h"] = int(template_h)
        if tap_match_x_pct is not None:
            body["tap_match_x_pct"] = float(tap_match_x_pct)
        if tap_match_y_pct is not None:
            body["tap_match_y_pct"] = float(tap_match_y_pct)
        if start_step_index:
            body["start_step_index"] = int(start_step_index)
        payload = json.dumps(body)
        # Score = run_at unix ts (earlier = higher priority in ZADD)
        qk = _queue_key(instance_id)
        await self._redis.zadd(qk, {payload: run_at})
        # Duplicate index (fast skip_if_duplicate): track payloads per signature.
        try:
            idx_region = None if dedup_ignore_region else region
            await self._redis.sadd(
                _dup_index_key(
                    instance_id=instance_id,
                    player_id=player_id,
                    task_type=task_type,
                    region=idx_region,
                ),
                payload,
            )
        except Exception:
            logger.debug("queue idx: sadd failed", exc_info=True)
        return True

    async def has_pending_duplicate(
        self,
        *,
        player_id: str,
        task_type: str,
        region: str | None,
        instance_id: str | None = None,
        ignore_region: bool = False,
    ) -> bool:
        """True if the queue already has a matching item.

        Matching rules:
        - Always filters by ``task_type``.
        - Filters by ``region`` unless ``ignore_region=True``.
        - Filters by ``instance_id`` when provided.
        - Device-level pushes (``player_id=""``) match any player on the same
          instance, so one queued item blocks re-pushing for all players.
        - Player-specific pushes match only the same ``player_id``.
        """
        idx_region = None if ignore_region else region
        # Fast path via index.
        if instance_id:
            try:
                # Device-level items (player_id="") block all players on instance.
                key_dev = _dup_index_key(
                    instance_id=instance_id,
                    player_id="",
                    task_type=task_type,
                    region=idx_region,
                )
                if int(await self._redis.scard(key_dev)) > 0:
                    # Self-heal: index may be stale (e.g. queue item removed but idx not cleaned).
                    if await self._scan_queue_for_duplicate(
                        player_id="",
                        task_type=task_type,
                        region=region,
                        instance_id=instance_id,
                        ignore_region=ignore_region,
                    ):
                        return True
                    with suppress(Exception):
                        await self._redis.delete(key_dev)
                if player_id:
                    key_p = _dup_index_key(
                        instance_id=instance_id,
                        player_id=player_id,
                        task_type=task_type,
                        region=idx_region,
                    )
                    if int(await self._redis.scard(key_p)) > 0:
                        if await self._scan_queue_for_duplicate(
                            player_id=player_id,
                            task_type=task_type,
                            region=region,
                            instance_id=instance_id,
                            ignore_region=ignore_region,
                        ):
                            return True
                        with suppress(Exception):
                            await self._redis.delete(key_p)
                        return False
                    return False
                # caller is device-level; checked key_dev above
                return False
            except Exception:
                logger.debug("queue idx: scard failed; falling back to scan", exc_info=True)

        # Fallback: scan the queue ZSET.
        return await self._scan_queue_for_duplicate(
            player_id=player_id,
            task_type=task_type,
            region=region,
            instance_id=instance_id or "",
            ignore_region=ignore_region,
        )

    async def _scan_queue_for_duplicate(
        self,
        *,
        player_id: str,
        task_type: str,
        region: str | None,
        instance_id: str,
        ignore_region: bool,
    ) -> bool:
        """Slow path: scan queue ZSET for a matching pending item."""
        import json

        want_region = "" if ignore_region else (str(region).strip() if region else "")
        device_level = not player_id
        all_items = await self._redis.zrangebyscore(_queue_key(instance_id), "-inf", "+inf")
        for raw in all_items:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if str(data.get("instance_id", "")) != instance_id:
                continue
            if str(data.get("task_type", "")) != task_type:
                continue
            if not device_level and str(data.get("player_id", "")) != player_id:
                continue
            if ignore_region:
                return True
            got = data.get("region")
            got_s = str(got).strip() if got is not None else ""
            if got_s == want_region:
                return True
        return False

    @staticmethod
    def _task_types_requiring_node() -> set[str]:
        """Task types from `scenarios/by_cron/*.yaml` that declare `node`.

        These tasks must not run while instance `current_screen` is empty/unknown.
        """
        repo_root = Path(__file__).resolve().parent.parent
        cron_dir = repo_root / "scenarios" / "by_cron"
        if not cron_dir.is_dir():
            return set()
        fp = RedisQueue._cron_dir_fingerprint(cron_dir)
        return RedisQueue._task_types_requiring_node_cached(fp)

    @staticmethod
    def _cron_dir_fingerprint(cron_dir: Path) -> tuple[str, tuple[tuple[str, int, int], ...]]:
        """Stable fingerprint for by_cron YAMLs for cache invalidation."""
        items: list[tuple[str, int, int]] = []
        for p in sorted(cron_dir.glob("*.yaml")):
            try:
                st = p.stat()
            except OSError:
                continue
            items.append((p.name, int(st.st_mtime_ns), int(st.st_size)))
        return (str(cron_dir), tuple(items))

    @staticmethod
    @lru_cache(maxsize=8)
    def _task_types_requiring_node_cached(
        fp: tuple[str, tuple[tuple[str, int, int], ...]]
    ) -> set[str]:
        cron_dir = Path(fp[0])
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

    @staticmethod
    def _task_types_device_level() -> set[str]:
        """Task types from `scenarios/**/*.yaml` that declare ``device_level: true``.

        These scenarios are explicitly safe to run without ``active_player`` —
        identity probes (``who_i_am``/``where_i_am``), tutorial dismissals
        (``skip_button``/``hand_pointer*``), and popup taps.  Everything else
        is treated as player-bound and gated until ``active_player`` is set.
        """
        repo_root = Path(__file__).resolve().parent.parent
        scen_dir = repo_root / "scenarios"
        if not scen_dir.is_dir():
            return set()
        fp = RedisQueue._scenarios_tree_fingerprint(scen_dir)
        return RedisQueue._task_types_device_level_cached(fp)

    @staticmethod
    def _scenarios_tree_fingerprint(
        scen_dir: Path,
    ) -> tuple[str, tuple[tuple[str, int, int], ...]]:
        """Stable fingerprint for the whole ``scenarios/`` tree (excludes drafts)."""
        items: list[tuple[str, int, int]] = []
        for p in sorted(scen_dir.rglob("*.yaml")):
            if "drafts" in {part.lower() for part in p.parts}:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            items.append((p.relative_to(scen_dir).as_posix(), int(st.st_mtime_ns), int(st.st_size)))
        return (str(scen_dir), tuple(items))

    @staticmethod
    @lru_cache(maxsize=8)
    def _task_types_device_level_cached(
        fp: tuple[str, tuple[tuple[str, int, int], ...]]
    ) -> set[str]:
        scen_dir = Path(fp[0])
        try:
            import yaml
        except Exception:
            return set()

        out: set[str] = set()
        for yml in sorted(scen_dir.rglob("*.yaml")):
            if "drafts" in {part.lower() for part in yml.parts}:
                continue
            try:
                raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            if raw.get("device_level") is not True:
                continue
            t = str(raw.get("task") or raw.get("task_type") or "").strip()
            if not t:
                t = yml.stem
            if t:
                out.add(t)
        return out

    async def pop_due(self, instance_id: str, *, current_screen: str = "") -> QueueItem | None:
        import json
        from contextlib import suppress

        now = time.time()
        # Fetch earliest due tasks (score <= now) for players on this instance.
        # Also allow device-level items with empty player_id ("") which will be
        # resolved to an active player at execution time.
        instance_players = self._players_for_instance(instance_id)
        # Include players discovered at runtime (OCR'd in-game id written by who_i_am).
        ap = ""
        try:
            raw_ap = await self._redis.hget(
                f"wos:instance:{instance_id}:state", "active_player"
            )
            ap = (raw_ap.decode() if isinstance(raw_ap, bytes) else str(raw_ap or "")).strip()
            if ap:
                instance_players = instance_players | {ap}
        except Exception:
            pass
        key = _queue_key(instance_id)
        candidates = await self._redis.zrangebyscore(key, "-inf", now)

        due: list[tuple[str, dict[str, object]]] = []
        for raw in candidates:
            data = json.loads(raw)
            pid = str(data.get("player_id", ""))
            if str(data.get("instance_id", "")) == instance_id and (pid == "" or pid in instance_players):
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

        # No active player yet: only run scenarios explicitly marked `device_level: true`
        # (identity probes, tutorial dismissals, popup taps).  Everything else is
        # player-bound and must wait until ``who_i_am`` populates ``active_player``.
        if not ap:
            device_level = self._task_types_device_level()
            due = [x for x in due if str(x[1].get("task_type") or "") in device_level]
            if not due:
                return None

        due.sort(
            key=lambda item: (
                -int(item[1].get("priority", 0)),
                float(item[1].get("run_at", now)),
            )
        )
        raw, data = due[0]
        await self._redis.zrem(key, raw)
        # Keep duplicate index in sync (best-effort).
        try:
            pid = str(data.get("player_id", ""))
            ttype = str(data.get("task_type", ""))
            reg_raw = data.get("region")
            reg_s = str(reg_raw).strip() if reg_raw is not None and str(reg_raw).strip() != "" else None
            # Remove from both: region-sensitive key and ignore-region key.
            with suppress(Exception):
                await self._redis.srem(
                    _dup_index_key(
                        instance_id=instance_id,
                        player_id=pid,
                        task_type=ttype,
                        region=reg_s,
                    ),
                    raw,
                )
            with suppress(Exception):
                await self._redis.srem(
                    _dup_index_key(
                        instance_id=instance_id,
                        player_id=pid,
                        task_type=ttype,
                        region=None,
                    ),
                    raw,
                )
        except Exception:
            logger.debug("queue idx: srem failed", exc_info=True)
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
        mtlx = data.get("match_top_left_x")
        mtly = data.get("match_top_left_y")
        tw = data.get("template_w")
        th = data.get("template_h")
        tmx = data.get("tap_match_x_pct")
        tmy = data.get("tap_match_y_pct")
        match_top_left_x = int(mtlx) if mtlx is not None else None
        match_top_left_y = int(mtly) if mtly is not None else None
        template_w = int(tw) if tw is not None else None
        template_h = int(th) if th is not None else None
        tap_match_x_pct = float(tmx) if tmx is not None else None
        tap_match_y_pct = float(tmy) if tmy is not None else None
        ssi = data.get("start_step_index")
        start_step_index = int(ssi) if ssi is not None else 0
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
            match_top_left_x=match_top_left_x,
            match_top_left_y=match_top_left_y,
            template_w=template_w,
            template_h=template_h,
            tap_match_x_pct=tap_match_x_pct,
            tap_match_y_pct=tap_match_y_pct,
            start_step_index=start_step_index,
        )

    async def peek_all(self) -> list[QueueItem]:
        import json

        results: list[QueueItem] = []
        keys: list[str] = []
        async for k in self._redis.scan_iter(match="wos:queue:*"):
            ks = k.decode() if isinstance(k, bytes) else str(k)
            if ":running" in ks:
                continue
            keys.append(ks)
        for key in keys:
            try:
                items = await self._redis.zrangebyscore(key, "-inf", "+inf", withscores=True)
            except Exception:
                continue
            for raw, score in items:
                data = json.loads(raw)
                reg = data.get("region")
                region = (
                    str(reg).strip() if reg is not None and str(reg).strip() != "" else None
                )
                tap_x = data.get("tap_x_pct")
                tap_y = data.get("tap_y_pct")
                tap_x_pct = float(tap_x) if tap_x is not None else None
                tap_y_pct = float(tap_y) if tap_y is not None else None
                thr = data.get("threshold")
                threshold = float(thr) if thr is not None else None
                sc = data.get("overlay_match_score")
                if sc is None:
                    sc = data.get("score")
                score_val = float(sc) if sc is not None else None
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
                        instance_id=str(data.get("instance_id") or ""),
                        region=region,
                        tap_x_pct=tap_x_pct,
                        tap_y_pct=tap_y_pct,
                        threshold=threshold,
                        score=score_val,
                        set_node=set_node,
                        dsl_scenario=dsl_scenario,
                    )
                )
        return results

    async def remove(self, task_id: str) -> None:
        import json
        from contextlib import suppress

        async for key in self._redis.scan_iter(match="wos:queue:*"):
            ks = key.decode() if isinstance(key, bytes) else str(key)
            if ":running" in ks:
                continue
            all_items = await self._redis.zrangebyscore(ks, "-inf", "+inf")
            for raw in all_items:
                data = json.loads(raw)
                if data["task_id"] == task_id:
                    await self._redis.zrem(ks, raw)
                    # Best-effort idx cleanup (both keys).
                    iid = str(data.get("instance_id") or "").strip() or "unknown"
                    pid = str(data.get("player_id") or "")
                    ttype = str(data.get("task_type") or "")
                    reg_raw = data.get("region")
                    reg_s = (
                        str(reg_raw).strip()
                        if reg_raw is not None and str(reg_raw).strip() != ""
                        else None
                    )
                    with suppress(Exception):
                        await self._redis.srem(
                            _dup_index_key(
                                instance_id=iid, player_id=pid, task_type=ttype, region=reg_s
                            ),
                            raw,
                        )
                    with suppress(Exception):
                        await self._redis.srem(
                            _dup_index_key(
                                instance_id=iid, player_id=pid, task_type=ttype, region=None
                            ),
                            raw,
                        )
                    return

    async def remove_by_task_type(self, task_type: str, instance_id: str) -> int:
        """Remove all queued items matching task_type + instance_id. Returns count removed."""
        import json
        from contextlib import suppress

        all_items = await self._redis.zrangebyscore(_queue_key(instance_id), "-inf", "+inf")
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
                await self._redis.zrem(_queue_key(instance_id), raw)
                pid = str(data.get("player_id") or "")
                reg_raw = data.get("region")
                reg_s = (
                    str(reg_raw).strip()
                    if reg_raw is not None and str(reg_raw).strip() != ""
                    else None
                )
                with suppress(Exception):
                    await self._redis.srem(
                        _dup_index_key(
                            instance_id=instance_id,
                            player_id=pid,
                            task_type=task_type,
                            region=reg_s,
                        ),
                        raw,
                    )
                with suppress(Exception):
                    await self._redis.srem(
                        _dup_index_key(
                            instance_id=instance_id,
                            player_id=pid,
                            task_type=task_type,
                            region=None,
                        ),
                        raw,
                    )
                removed += 1
        return removed

    def _players_for_instance(self, instance_id: str) -> set[str]:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return set(player_ids_for_device(inst.bluestacks_window_title))
        return set()
