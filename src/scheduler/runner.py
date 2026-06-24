from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING

import redis as _redis_sync
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from config.devices import player_ids_for_device_candidates
from config.paths import repo_root
from config.redis_health import ping_async_redis_or_exit
from dsl.cron_specs import (
    iter_cron_yaml_files_for_repo,
    resolve_cron_priority,
    resolve_cron_task_type,
)
from scheduler.queue import RedisQueue
from scheduler.wake import WAKE_CHANNEL

if TYPE_CHECKING:
    from config.loader import Settings

logger = logging.getLogger(__name__)

_SCHEDULER_UI_QUEUE = WAKE_CHANNEL
_CRON_KEY = "wos:scheduler:cron:last_run"

# Gift codes — global poller, see SchedulerRunner._run_gift_codes_polling.
# (game_id, module path with poll_once + run_gift_code_redeemer, redeem-coordination
# lock key shared with games/<game>/gift_codes/exec.py manual UI path, redeem_supported)
_GIFT_CODE_GAMES: list[tuple[str, str, str, bool]] = [
    ("wos", "century.gift_codes.wos", "wos:gift_code_redeem:lock", True),
    ("kingshot", "century.gift_codes.kingshot", "wos:gift_code_redeem:lock:kingshot", True),
    ("wos_beta", "century.gift_codes.wos_beta", "wos:gift_code_redeem:lock:wos_beta", False),
    (
        "kingshot_beta",
        "century.gift_codes.kingshot_beta",
        "wos:gift_code_redeem:lock:kingshot_beta",
        False,
    ),
]
_GIFT_CODE_POLL_INTERVAL_S = 6 * 60 * 60
_GIFT_CODE_LOCK_TTL_S = 2 * 60 * 60
_BACKGROUND_GIFT_CODE_TASKS: set[asyncio.Task[None]] = set()

# Atomic compare-and-delete: only drop the lock if we still own the token.
# A non-atomic GET-then-DELETE races with a manual trigger that re-acquires
# the key after our TTL expires, which would let two redeemers run at once.
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class SchedulerRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        redis: aioredis.Redis | None = None,  # type: ignore[type-arg]
        queue: RedisQueue | None = None,
        wake_sync: _redis_sync.Redis | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._wake_sync = wake_sync
        self._queue = queue
        self._owns_redis = redis is None
        self._owns_wake_sync = wake_sync is None
        # Last stamina decision signature per player — lets the planner skip
        # rewriting the trace when its decision hasn't changed tick-to-tick.
        self._stamina_last_sig: dict[str, str] = {}
        # Same, for the resource-world (march slots / troops / heroes) planner.
        self._resource_last_sig: dict[str, str] = {}

    async def _connect(self) -> None:
        from config.redis_metrics import instrument_redis_client

        url = self._settings.redis.url
        if self._redis is None:
            # The run loop does a blocking `blpop(timeout=interval)` for its
            # event-driven heartbeat. redis-py applies `socket_connect_timeout`
            # as the read timeout too when `socket_timeout` is unset, so without
            # this any blpop longer than ~5s raised TimeoutError mid-wait
            # (degrading the wait to a tight retry; see the run-loop guard).
            # Give the read timeout headroom over the blpop interval so the pop
            # blocks its full duration and returns cleanly.
            read_timeout = float(self._settings.scheduler.interval_seconds) + 5.0
            self._redis = aioredis.from_url(
                url, socket_connect_timeout=5.0, socket_timeout=read_timeout
            )
            instrument_redis_client(self._redis, component="scheduler")
            await ping_async_redis_or_exit(self._redis, url=url)
        if self._queue is None:
            self._queue = RedisQueue(self._redis, self._settings)
        if self._wake_sync is None:
            self._wake_sync = _redis_sync.Redis.from_url(url, socket_connect_timeout=5.0)
            instrument_redis_client(self._wake_sync, component="scheduler")

    async def _disconnect_redis(self) -> None:
        client = self._redis
        sync_client = self._wake_sync
        self._redis = None
        self._wake_sync = None
        self._queue = None
        if sync_client is not None and self._owns_wake_sync:
            try:
                sync_client.close()
            except Exception:
                logger.debug("Scheduler wake-sync Redis close failed", exc_info=True)
        if client is None or not self._owns_redis:
            return
        try:
            await client.aclose()
        except Exception:
            logger.debug("Scheduler Redis aclose failed", exc_info=True)

    async def _drain_background_tasks(self, timeout_s: float = 30.0) -> None:
        """Let in-flight gift-code tasks finish before Redis closes.

        Each task releases its 2h redeem lock in a ``finally`` that talks to
        Redis. If we disconnect first, that release fails and the lock leaks
        for its full TTL. Wait (bounded) for them, then cancel stragglers.
        """
        pending = [t for t in _BACKGROUND_GIFT_CODE_TASKS if not t.done()]
        if not pending:
            return
        _done, still_pending = await asyncio.wait(pending, timeout=timeout_s)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)

    async def _instance_current_screen(self, instance_id: str) -> str:
        assert self._redis is not None
        raw = await self._redis.hget(f"wos:instance:{instance_id}:state", "current_screen")
        if raw is None:
            return ""
        s = raw.decode() if isinstance(raw, bytes) else str(raw)
        return s.strip()

    async def _instance_furnace_level(self, instance_id: str) -> int:
        """Furnace level from instance state (written by the onboarding reader).

        Used to keep onboarding-sensitive crons (``min_furnace_level:``) out of
        the queue while the tutorial is still running. Missing/unparseable → 0.
        """
        assert self._redis is not None
        raw = await self._redis.hget(
            f"wos:instance:{instance_id}:state", "buildings.furnace.level"
        )
        s = (raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip()
        try:
            return int(s) if s else 0
        except ValueError:
            return 0

    async def _instance_has_active_player(self, instance_id: str) -> bool:
        """Whether ``who_i_am`` has resolved a player for this instance.

        A resolved ``active_player`` means the tutorial is done (``who_i_am`` is
        itself gated until furnace >= 5), so it's a reliable "past onboarding"
        signal even when the furnace-level reader hasn't populated the level.
        """
        assert self._redis is not None
        raw = await self._redis.hget(f"wos:instance:{instance_id}:state", "active_player")
        return bool((raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip())

    @staticmethod
    def _cron_due(expr: str, now: float) -> bool:
        """Minimal cron matcher supporting:

        - "*/N * * * *"  (every N minutes)
        - "M */H * * *"  (minute M, every H hours)
        """
        expr = (expr or "").strip().strip('"').strip("'")
        parts = expr.split()
        if len(parts) != 5:
            return False
        minute, hour, _dom, _mon, _dow = parts
        lt = time.localtime(now)
        m = lt.tm_min
        h = lt.tm_hour

        if minute.startswith("*/") and hour == "*":
            try:
                n = int(minute[2:])
            except ValueError:
                return False
            return n > 0 and (m % n == 0)

        if hour.startswith("*/"):
            try:
                hh = int(hour[2:])
                mm = int(minute)
            except ValueError:
                return False
            return hh > 0 and (m == mm) and (h % hh == 0)

        return False

    @staticmethod
    def _cron_interval_seconds(expr: str) -> int | None:
        """Return the interval for cron shapes supported by this scheduler.

        The scheduler intentionally supports only the two shapes used by our
        maintenance specs. For those, we can keep a concrete future queue item
        instead of relying on hitting the exact cron minute.
        """
        expr = (expr or "").strip().strip('"').strip("'")
        parts = expr.split()
        if len(parts) != 5:
            return None
        minute, hour, _dom, _mon, _dow = parts

        if minute.startswith("*/") and hour == "*":
            try:
                n = int(minute[2:])
            except ValueError:
                return None
            return n * 60 if n > 0 else None

        if hour.startswith("*/"):
            try:
                hh = int(hour[2:])
                int(minute)
            except ValueError:
                return None
            return hh * 60 * 60 if hh > 0 else None

        return None

    async def _task_already_running(
        self, *, instance_id: str, player_id: str, task_type: str
    ) -> bool:
        """True if a matching task is currently in flight on this instance.

        Reads the per-instance running key (``wos:queue:running:{instance_id}``)
        published by the worker for every task it pops (see
        ``worker/instance_worker_tasks.py``). The pending-queue dedup
        (``has_pending_duplicate``) only catches duplicates that are still
        in the sorted set — once the worker pops a long-running item the
        queue is empty and a fresh scheduler tick would otherwise re-enqueue
        the same logical task.
        """
        assert self._redis is not None
        raw = await self._redis.get(f"wos:queue:running:{instance_id}")
        if raw is None:
            return False
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        if str(data.get("task_type") or "") != task_type:
            return False
        running_player = str(data.get("player_id") or "")
        return not player_id or running_player == player_id

    async def _ensure_interval_cron_item(
        self,
        *,
        name: str,
        spec_slug: str,
        expr: str,
        task_type: str,
        priority: int,
        instance_id: str,
        player_id: str,
        interval_s: int,
        now: float,
    ) -> None:
        """Publish the cron spec at the right ``run_at`` and throttle re-enqueue.

        ``run_at`` honors the historical cadence: if ``recent_runs`` shows
        the task last fired ``T`` seconds ago and the cron interval is ``I``,
        we schedule ``run_at = now + max(0, I - T)`` so a restart doesn't
        re-fire a 4-hour cron the moment we boot. Cold start (no history)
        falls through to ``run_at = now`` — first run on a fresh setup
        shouldn't wait an interval to debut.

        A Redis throttle key (TTL=``interval_s``) still gates subsequent
        scheduler ticks (default cadence: 30s) so the same task isn't
        re-enqueued the moment the worker pops it. ``has_pending_duplicate``
        + ``_task_already_running`` cover the inverse: don't enqueue while one
        is already pending or in flight.
        """
        assert self._queue is not None and self._redis is not None
        if await self._task_already_running(
            instance_id=instance_id,
            player_id=player_id,
            task_type=task_type,
        ):
            return
        if await self._queue.has_pending_duplicate(
            player_id=player_id,
            task_type=task_type,
            region=None,
            instance_id=instance_id,
            ignore_region=True,
        ):
            return

        last_run = await self._queue.last_run_at(
            instance_id=instance_id,
            task_type=task_type,
            player_id=player_id,
        )
        run_at = now if last_run is None else max(now, last_run + float(interval_s))

        throttle_key = (
            f"wos:scheduler:cron_throttle:{spec_slug}:{instance_id}:{player_id}"
        )
        acquired = bool(
            await self._redis.set(throttle_key, "1", nx=True, ex=int(interval_s))
        )
        if not acquired:
            return
        enqueued = await self._queue.schedule(
            task_id=f"cron:{spec_slug}:{player_id}:{int(run_at)}",
            player_id=player_id,
            task_type=task_type,
            priority=priority,
            run_at=run_at,
            instance_id=instance_id,
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )
        if enqueued:
            delay_s = max(0.0, run_at - now)
            logger.info(
                "Cron scheduled: %s (%s) %s for %s/%s at %s (delay=%.0fs, last_run=%s)",
                name,
                expr,
                task_type,
                instance_id,
                player_id,
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run_at)),
                delay_s,
                "never" if last_run is None
                else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_run)),
            )

    async def _record_recent_runs_history_depth(self, now: float) -> None:
        """Emit oldest-entry-age + size of ``recent_runs`` per instance.

        Best-effort — failures are debug-logged and silently dropped. Keeping
        the read out of the hot enqueue path (its own helper) means a Redis
        flap won't take down cron scheduling.
        """
        assert self._queue is not None
        from config.tracing import (
            recent_runs_history_age_histogram,
            recent_runs_history_size_histogram,
        )

        age_hist = recent_runs_history_age_histogram()
        size_hist = recent_runs_history_size_histogram()
        for inst in self._settings.instances:
            iid = inst.instance_id
            try:
                age = await self._queue.oldest_recent_run_age(
                    instance_id=iid, now=now
                )
                size = await self._redis.zcard(  # type: ignore[union-attr]
                    f"wos:instance:{iid}:recent_runs"
                )
            except Exception:
                logger.debug("recent_runs history depth probe failed", exc_info=True)
                continue
            if age is not None:
                age_hist.record(float(age), attributes={"instance_id": iid})
            size_hist.record(int(size or 0), attributes={"instance_id": iid})

    async def _run_cron_specs(self) -> None:
        """Enqueue cron-based jobs (no extra checks beyond cron)."""
        assert self._redis is not None and self._queue is not None
        now = time.time()
        await self._record_recent_runs_history_depth(now)
        root = repo_root()
        cron_ymls = iter_cron_yaml_files_for_repo(root)
        if not cron_ymls:
            return

        # Focus mode: instances pinned to a single scenario must not have cron
        # work piled into their queue (the worker would only drop it). Compute
        # the focused set once per tick rather than per spec×instance.
        focused_instances: set[str] = set()
        for inst in self._settings.instances:
            try:
                raw_focus = await self._redis.hget(
                    f"wos:instance:{inst.instance_id}:state", "focus_scenario"
                )
            except Exception:
                raw_focus = None
            fs = (
                raw_focus.decode() if isinstance(raw_focus, bytes) else str(raw_focus or "")
            ).strip()
            if fs:
                focused_instances.add(inst.instance_id)

        import yaml

        for yml in cron_ymls:
            try:
                raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                logger.exception("Cron spec load failed: %s", yml)
                continue
            if not isinstance(raw, dict):
                continue
            enabled = bool(raw.get("enabled", True))
            if not enabled:
                continue
            expr = str(raw.get("cron") or "").strip()
            # Single source of truth for both task_type + priority fallbacks
            # — UI Cron Push uses the same helpers (``scenarios.cron_specs``)
            # so manual pushes match the scheduled enqueue exactly.
            task_type = resolve_cron_task_type(raw, yml)
            prio = resolve_cron_priority(raw.get("priority"))
            name = str(raw.get("name") or "").strip() or yml.stem
            when_current_screen = str(raw.get("when_current_screen") or "").strip().lower()
            try:
                min_furnace_level = int(raw.get("min_furnace_level") or 0)
            except (TypeError, ValueError):
                min_furnace_level = 0
            if not expr or not task_type:
                continue

            # Use `name` as the human identifier; normalize to a slug for keys.
            spec_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-") or yml.stem
            interval_s = self._cron_interval_seconds(expr)
            for inst in self._settings.instances:
                if inst.instance_id in focused_instances:
                    continue
                if when_current_screen:
                    current_screen = await self._instance_current_screen(inst.instance_id)
                    if when_current_screen in {"unknown", "none", "empty"}:
                        if current_screen:
                            continue
                    else:
                        if current_screen.lower() != when_current_screen:
                            continue
                # Onboarding gate: keep this cron out of the queue while the
                # tutorial is still running. Furnace level is the primary signal,
                # but its reader isn't always populated (reads 0), so ALSO treat a
                # resolved active_player as "past onboarding" — who_i_am only runs
                # once the tutorial is done. Without this, a developed account
                # whose furnace level isn't in state stays gated forever and never
                # gets its return-home cron, stranding the bot on whatever screen a
                # no-node read cron leaves it. Gating at the publish point (not a
                # scenario cond) avoids enqueue+bail spam for a device-level cron.
                if (
                    min_furnace_level > 0
                    and not await self._instance_has_active_player(inst.instance_id)
                    and await self._instance_furnace_level(inst.instance_id) < min_furnace_level
                ):
                    continue
                player_ids = player_ids_for_device_candidates(
                    inst.bluestacks_window_title,
                    inst.instance_id,
                )
                for player_id in player_ids:
                    if interval_s is not None:
                        await self._ensure_interval_cron_item(
                            name=name,
                            spec_slug=spec_slug,
                            expr=expr,
                            task_type=task_type,
                            priority=prio,
                            instance_id=inst.instance_id,
                            player_id=player_id,
                            interval_s=interval_s,
                            now=now,
                        )
                        continue

                    if not self._cron_due(expr, now):
                        continue
                    # Once-per-minute guard (scheduler ticks faster than cron granularity).
                    guard = f"{spec_slug}:{inst.instance_id}:{player_id}:{int(now // 60)}"
                    if await self._redis.hget(_CRON_KEY, guard):  # type: ignore[arg-type]
                        continue
                    await self._redis.hset(_CRON_KEY, guard, "1")
                    await self._redis.expire(_CRON_KEY, 120)

                    await self._queue.schedule(
                        task_id=f"cron:{spec_slug}:{player_id}:{int(now)}",
                        player_id=player_id,
                        task_type=task_type,
                        priority=prio,
                        run_at=now,
                        instance_id=inst.instance_id,
                        skip_if_duplicate=True,
                        dedup_ignore_region=True,
                    )
                    logger.info(
                        "Cron enqueued: %s (%s) %s for %s/%s",
                        name,
                        expr,
                        task_type,
                        inst.instance_id,
                        player_id,
                    )

    async def _load_player_states(self) -> dict[str, dict[str, object]]:
        # ``_connect`` runs before any tick, so ``_redis`` is always populated here.
        assert self._redis is not None
        states: dict[str, dict[str, object]] = {}
        for inst in self._settings.instances:
            for player_id in player_ids_for_device_candidates(
                inst.bluestacks_window_title,
                inst.instance_id,
            ):
                key = f"wos:player:{player_id}:state"
                raw = await self._redis.hgetall(key)
                state = {
                    (k.decode() if isinstance(k, bytes) else k): (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in raw.items()
                }
                state["player_id"] = player_id
                states[player_id] = state
        return states

    async def _build_player_instance_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for inst in self._settings.instances:
            for player_id in player_ids_for_device_candidates(
                inst.bluestacks_window_title,
                inst.instance_id,
            ):
                mapping[player_id] = inst.instance_id
        return mapping

    async def _ensure_daily_snapshot(self) -> None:
        """Snapshot every persisted gamer once per UTC day, regardless of worker activity.

        Why: ``record_player_stats`` already upserts on every state mutation, but an
        idle worker means no row gets written for the day. This guarantees one row
        per (player, day) using an atomic Redis ``SET NX EX`` so multiple
        scheduler ticks don't redo the work.
        """
        assert self._redis is not None
        from datetime import UTC, datetime

        day = datetime.now(tz=UTC).date().isoformat()
        key = f"wos:scheduler:daily_snapshot:{day}"
        # 30h TTL — long enough to outlast the day, short enough to expire before
        # the same key could be reused next UTC day.
        acquired = await self._redis.set(key, "1", nx=True, ex=30 * 3600)
        if not acquired:
            return

        def _snapshot_all() -> None:
            from config.state_sqlite import record_player_stats
            from config.state_store import get_state_store

            store = get_state_store()
            for pid in store.all_player_ids():
                gs = store.get(pid)
                if gs is None:
                    continue
                try:
                    record_player_stats(gs.snapshot())
                except Exception:
                    logger.exception("daily snapshot failed for player=%s", pid)

        try:
            await asyncio.to_thread(_snapshot_all)
            logger.info("Daily player snapshot recorded for %s", day)
        except Exception:
            logger.exception("Daily snapshot routine failed")

    async def _run_gift_codes_polling(self) -> None:
        """Scrape gift codes once globally per game, every 6 hours.

        Replaces the per-account cron fan-out previously driven by
        ``games/<game>/gift_codes/scenarios/by_cron/redeem_gift_codes.yaml``.
        That scenario was scheduled per (device × player), so on a multi-account
        setup the scrape hit the public aggregator N times and N-1 redeem
        tasks just bounced off the in-flight Redis lock. Gift-code work has
        no per-account or per-device state — driving it from the scheduler
        runs each step exactly once and keeps the bot queue free.

        Live-game redeem coordination with the UI manual-trigger path is via the same
        ``wos:gift_code_redeem:lock[:game]`` key the exec handler uses, so a
        user clicking *Redeem now* while the scheduler is mid-cycle (or
        vice-versa) sees *already running* instead of racing.

        Beta games are scrape-only here because codes apply inside the beta
        game client for the currently logged-in player.
        """
        assert self._redis is not None

        for game_id, module_path, redeem_lock_key, redeem_supported in _GIFT_CODE_GAMES:
            # 30s scheduler ticks must not re-fire inside the 6-hour cron
            # window. Atomic SET NX EX; first boot acquires immediately
            # (no key) so cold start runs once right away.
            cadence_key = f"wos:scheduler:gift_codes_poll:{game_id}"
            acquired = await self._redis.set(
                cadence_key, "1", nx=True, ex=_GIFT_CODE_POLL_INTERVAL_S,
            )
            if not acquired:
                continue

            if not redeem_supported:
                task = asyncio.create_task(
                    self._gift_codes_scrape_once(game_id, module_path),
                    name=f"gift-codes-poll-{game_id}",
                )
                _BACKGROUND_GIFT_CODE_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_GIFT_CODE_TASKS.discard)
                continue

            token = f"scheduler:{game_id}:{int(time.time())}"
            redeem_held = await self._redis.set(
                redeem_lock_key, token, nx=True, ex=_GIFT_CODE_LOCK_TTL_S,
            )
            if not redeem_held:
                logger.info(
                    "gift_codes_poll[%s]: redeem lock held by another caller; skip",
                    game_id,
                )
                continue

            task = asyncio.create_task(
                self._gift_codes_run_once(
                    game_id, module_path, redeem_lock_key, token,
                ),
                name=f"gift-codes-poll-{game_id}",
            )
            _BACKGROUND_GIFT_CODE_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_GIFT_CODE_TASKS.discard)

    async def _gift_codes_scrape_once(self, game_id: str, module_path: str) -> None:
        """One scrape-only cycle for beta codes that are applied in the game UI."""
        try:
            mod = importlib.import_module(module_path)
            new_codes = await mod.poll_once()
            logger.info(
                "gift_codes_poll[%s]: scrape found %d new code(s), redeem skipped",
                game_id,
                len(new_codes),
            )
        except Exception:
            logger.exception("gift_codes_poll[%s]: scrape failed", game_id)

    async def _gift_codes_run_once(
        self,
        game_id: str,
        module_path: str,
        redeem_lock_key: str,
        token: str,
    ) -> None:
        """One scrape+redeem cycle. Releases the redeem lock only if we still own it."""
        assert self._redis is not None
        try:
            mod = importlib.import_module(module_path)
            try:
                new_codes = await mod.poll_once()
                logger.info(
                    "gift_codes_poll[%s]: scrape found %d new code(s)",
                    game_id,
                    len(new_codes),
                )
            except Exception:
                logger.exception("gift_codes_poll[%s]: scrape failed", game_id)
            try:
                summary = await mod.run_gift_code_redeemer()
                counts = summary.counts_by_status()
                counts_s = ", ".join(f"{k}={v}" for k, v in counts.items()) or "nothing"
                logger.info(
                    "gift_codes_poll[%s]: redeem done total=%d %s",
                    game_id,
                    len(summary.results),
                    counts_s,
                )
            except Exception:
                logger.exception("gift_codes_poll[%s]: redeem failed", game_id)
        finally:
            # Only release if we still own the token — a parallel manual
            # trigger may have replaced it (e.g. after our TTL). Compare and
            # delete atomically so we never delete someone else's lock.
            try:
                await self._redis.eval(
                    _RELEASE_LOCK_LUA, 1, redeem_lock_key, token,
                )
            except Exception:
                logger.debug(
                    "gift_codes_poll[%s]: lock release failed",
                    game_id,
                    exc_info=True,
                )

    async def _run_once(self) -> None:
        # ``_connect`` runs before any tick, so both ``_queue`` and ``_redis``
        # are always populated here.
        assert self._queue is not None
        await self._ensure_daily_snapshot()
        await self._run_gift_codes_polling()
        await self._run_cron_specs()
        player_states = await self._load_player_states()
        player_instance_map = await self._build_player_instance_map()
        now = time.time()
        await self._run_stamina_planner(player_states, player_instance_map, now)
        await self._run_resource_planner(player_states, player_instance_map, now)
        await self._run_march_planner(player_states, player_instance_map, now)
        await self._run_fleet_coordinator(now)

    async def _run_fleet_coordinator(self, now: float) -> None:
        """Cross-account orchestrator: drive multi-account campaigns over the
        coord directive bus. Runs last in the tick (it arbitrates across all
        instances the per-player planners already filled).

        Dormant unless ``games/wos/core/fleet/fleet.yaml`` enables a campaign
        (ships all ``false``) — like the stamina/march planners, it bails before
        any IO when disabled. Each run is isolated by a per-run lease (one driver
        only) and try/except so one stuck campaign can't stall the tick.
        """
        assert self._redis is not None and self._queue is not None
        from games.wos.core.fleet import adapter as fleet

        from coord.bus import DirectiveBus
        from coord.campaign import arbitrate

        try:
            defs = fleet.load_campaigns()   # mtime-cached; no disk read per tick
        except Exception:
            logger.warning("fleet campaign load failed", exc_info=True)
            return
        active = [d for d in defs.values() if d.enabled]
        if not active:
            return

        try:
            candidates, planner_fleet, calendar = await fleet.build_inputs(
                self._redis, self._settings, now
            )
        except Exception:
            logger.warning("fleet input build failed", exc_info=True)
            return
        bus = DirectiveBus(self._redis)

        # 1. Gather every active run across all enabled campaigns.
        pairs = []
        for cdef in active:
            try:
                runs = await fleet.active_runs_for(
                    self._redis, cdef, candidates, calendar, now
                )
            except Exception:
                logger.warning("fleet active_runs failed campaign=%s", cdef.id, exc_info=True)
                continue
            pairs.extend((cdef, run) for run in runs)
        if not pairs:
            return

        # 2. Safety filter: suppress runs that send troops out during an
        #    alliance-war/bear-hunt window, and raids touching an active-event
        #    participant. Suppressed runs HOLD (reported in the bottleneck).
        safe_pairs, suppressed = fleet.partition_by_safety(pairs, calendar)
        if suppressed:
            logger.info("fleet: %d run(s) suppressed by safety: %s", len(suppressed), suppressed)

        # 3. Arbitrate the shared resources (accounts/devices) across the runs:
        #    the highest-priority conflict-free set wins this tick (a reinforcement
        #    preempts a raid sharing its fighter; two accounts on one device don't
        #    thrash between campaigns).
        claims = [fleet.build_claim(cdef, run, now) for cdef, run in safe_pairs]
        result = arbitrate(claims)
        winners = set(result.active)

        # 4. Dispatch only the runs that won their resources; the rest HOLD and
        #    retry next tick. Each winner is isolated by its per-run lease.
        for cdef, run in safe_pairs:
            if run.run_id not in winners:
                continue
            lock = fleet.campaign_lock(self._redis, run.run_id)
            token = await lock.acquire(ttl_s=int(cdef.default_ttl_s))
            if token is None:
                continue  # another driver holds this run
            try:
                await fleet.run_campaign_tick(
                    self._redis, self._queue, bus, cdef, run, planner_fleet, calendar, now
                )
            except Exception:
                logger.warning("fleet tick failed run=%s", run.run_id, exc_info=True)
            finally:
                await lock.release(token)

        # 5. Publish the contention + suppression snapshot (observability).
        try:
            await fleet.write_fleet_bottleneck(
                self._redis, result, now, suppressed=suppressed
            )
        except Exception:
            logger.debug("fleet bottleneck write failed", exc_info=True)

    async def _run_stamina_planner(
        self,
        player_states: dict[str, dict[str, object]],
        player_instance_map: dict[str, str],
        now: float,
    ) -> None:
        """Distribute the shared stamina pool across competing consumers.

        Dormant unless ``games/wos/core/stamina/budget.yaml`` sets
        ``enabled: true`` — until the consumer scenarios + OCR region exist,
        enqueuing them would only hand the worker tasks it can't run. Each
        player is independent and fully isolated by try/except so a single bad
        snapshot can't stall the scheduler tick.
        """
        assert self._queue is not None and self._redis is not None
        from games.wos.core.stamina import adapter as stamina

        try:
            budget = stamina.load_budget()   # mtime-cached; no disk read per tick
        except Exception:
            logger.warning("stamina budget load failed", exc_info=True)
            return
        if not budget.enabled:
            return

        for player_id, state in player_states.items():
            instance_id = player_instance_map.get(player_id, "")
            if not instance_id:
                continue
            try:
                result = stamina.plan(budget, state, now)
                dec = result.decision
                # Only record the trace when the decision actually changes —
                # otherwise the ring-buffer floods with identical entries every
                # heartbeat. (Enqueue below still runs every tick; it's cheap and
                # idempotent via skip_if_duplicate + the running-key guard.)
                sig = stamina.decision_signature(dec)
                if self._stamina_last_sig.get(player_id) != sig:
                    await stamina.write_decision_trace(self._redis, player_id, result, now)
                    self._stamina_last_sig[player_id] = sig
                await stamina.prune_stale_quota(
                    self._redis, player_id, state, result.period
                )
                if dec.action not in (stamina.CONSUME, stamina.SUPPLY):
                    continue
                # Same guard the cron path uses: ``skip_if_duplicate`` only sees
                # the pending set, so re-check the running key to avoid a second
                # copy while the worker is mid-execution.
                if await self._task_already_running(
                    instance_id=instance_id,
                    player_id=player_id,
                    task_type=dec.task_type or "",
                ):
                    continue
                await stamina.enqueue_decision(
                    self._queue,
                    instance_id=instance_id,
                    player_id=player_id,
                    decision=dec,
                    period=result.period,
                    now=now,
                )
            except Exception:
                logger.warning(
                    "stamina planner failed for player=%s", player_id, exc_info=True
                )

    async def _run_resource_planner(
        self,
        player_states: dict[str, dict[str, object]],
        player_instance_map: dict[str, str],
        now: float,
    ) -> None:
        """Allocate the shared resource world across competing raids/marches.

        Dormant unless ``games/wos/core/resources/actions.yaml`` sets
        ``enabled: true``. Until the troop-pool + hero-roster readers exist, the
        ``observed: false`` + ``unobserved_policy: block`` config holds every
        action back, so enabling early cannot fire a march we can't staff. Each
        player is isolated by try/except so one bad snapshot can't stall the tick.
        """
        assert self._queue is not None and self._redis is not None
        from games.wos.core.resources import adapter as resources

        try:
            table = resources.load_table()   # mtime-cached; no disk read per tick
        except Exception:
            logger.warning("resource action table load failed", exc_info=True)
            return
        if not table.enabled:
            return

        for player_id, state in player_states.items():
            instance_id = player_instance_map.get(player_id, "")
            if not instance_id:
                continue
            try:
                # The ledger holds each chosen action's whole cost vector with a
                # TTL — read (and prune) it so the plan sees resources already
                # promised this tick (closes the dispatch→OCR over-allocation gap).
                ledger = await resources.read_ledger(self._redis, player_id, now)
                result = resources.plan(table, state, now, ledger)
                dec = result.decision
                sig = resources.decision_signature(dec)
                if self._resource_last_sig.get(player_id) != sig:
                    await resources.write_decision_trace(
                        self._redis, player_id, result, now
                    )
                    self._resource_last_sig[player_id] = sig
                if dec.action != resources.CONSUME:
                    continue
                if await self._task_already_running(
                    instance_id=instance_id,
                    player_id=player_id,
                    task_type=dec.task_type or "",
                ):
                    continue
                reservation = await resources.reserve(self._redis, player_id, dec, now)
                await resources.enqueue_decision(
                    self._queue,
                    instance_id=instance_id,
                    player_id=player_id,
                    decision=dec,
                    period=result.period,
                    reservation=reservation,
                    now=now,
                )
            except Exception:
                logger.warning(
                    "resource planner failed for player=%s", player_id, exc_info=True
                )

    async def _run_march_planner(
        self,
        player_states: dict[str, dict[str, object]],
        player_instance_map: dict[str, str],
        now: float,
    ) -> None:
        """Fill idle march slots with the best MARCH-spending candidate.

        Dispatch-blind: per player, the coordinator picks a blind intel run and
        any active timed events (Romance Season, …) for the free march slots and
        queues them. Runs AFTER the resource planner so it sees that tick's slot
        holds (``build_world`` subtracts the ledger). Gated by its OWN switch
        (``coordinator/march.yaml`` ``enabled``) — independent of
        ``resources/actions.yaml`` ``enabled``, since intel/events need only the
        slot count + stamina, not the troop/hero readers. Each player is isolated
        by try/except so one bad snapshot can't stall the tick.
        """
        assert self._queue is not None and self._redis is not None
        from games.wos.core.coordinator.dispatch import load_march_config, run_march_tick
        from games.wos.core.resources import adapter as resources

        try:
            cfg = load_march_config()       # mtime-cached; no disk read per tick
        except Exception:
            logger.warning("march config load failed", exc_info=True)
            return
        if not cfg.enabled:
            return
        try:
            table = resources.load_table()  # for build_world's slot accounting only
        except Exception:
            logger.warning("march planner: resource table load failed", exc_info=True)
            return

        for player_id, state in player_states.items():
            instance_id = player_instance_map.get(player_id, "")
            if not instance_id:
                continue
            try:
                ledger = await resources.read_ledger(self._redis, player_id, now)
                world = resources.build_world(table, state, now, ledger)
                await run_march_tick(
                    queue=self._queue,
                    redis=self._redis,
                    instance_id=instance_id,
                    player_id=player_id,
                    now=now,
                    idle_slots=world.slots_free,
                    state=state,
                    cooldown_s=cfg.intel_cooldown_s,
                )
            except Exception:
                logger.warning(
                    "march planner failed for player=%s", player_id, exc_info=True
                )

    async def _drain_wake_queue(self) -> bool:
        """Pop any remaining wake messages without blocking. Returns True if
        an ``optimize_now`` command was seen (caller may want to log/trace it)."""
        assert self._redis is not None
        saw_optimize_now = False
        while True:
            raw = await self._redis.rpop(_SCHEDULER_UI_QUEUE)
            if raw is None:
                return saw_optimize_now
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            # Tolerate non-dict payloads (a future producer could push a list
            # or scalar) — without this guard ``data.get("cmd")`` raises
            # AttributeError and kills the drain loop, stranding any later
            # wake messages until the next heartbeat.
            if not isinstance(data, dict):
                continue
            if str(data.get("cmd")) == "optimize_now":
                saw_optimize_now = True

    async def run(self) -> None:
        try:
            await self._connect()
            assert self._redis is not None
            interval = self._settings.scheduler.interval_seconds
            logger.info(
                "Scheduler started, heartbeat=%ds (event-driven on %s)",
                interval,
                _SCHEDULER_UI_QUEUE,
            )

            # Run once at boot so a cold-started worker sees its queue populated
            # without waiting for the first wake signal.
            try:
                await self._run_once()
            except Exception:
                logger.exception("Scheduler loop error (initial)")

            wake_pop_failing = False
            while True:
                # Block until any producer publishes a wake (state change,
                # task completion, UI command), or until the heartbeat fires.
                # Drain the rest so a burst of wakes collapses to one optimize.
                try:
                    wake = await self._redis.blpop(_SCHEDULER_UI_QUEUE, timeout=interval)
                    if wake_pop_failing:
                        logger.info("Scheduler wake-pop recovered")
                        wake_pop_failing = False
                    if wake is not None:
                        await self._drain_wake_queue()
                except (TimeoutError, RedisError) as exc:
                    # A Redis read timeout / connection blip on the blocking
                    # heartbeat pop must NOT kill the scheduler. Under the
                    # multi-process supervisor it would just restart, but a
                    # single-process runner dies outright (and any coupled worker
                    # wedges with it). Treat it as a missed heartbeat: back off
                    # briefly, then fall through to a normal tick. Warn once on
                    # the transition (then debug) so a persistently short read
                    # timeout doesn't spam the log every interval.
                    if not wake_pop_failing:
                        logger.warning(
                            "Scheduler wake-pop failing (%r); ticking on backoff "
                            "until it recovers", exc
                        )
                        wake_pop_failing = True
                    else:
                        logger.debug("Scheduler wake-pop still failing (%r)", exc)
                    await asyncio.sleep(min(float(interval), 5.0))

                try:
                    await self._run_once()
                except Exception:
                    logger.exception("Scheduler loop error")
        finally:
            await self._drain_background_tasks()
            await self._disconnect_redis()


def main() -> None:
    from config.runtime_bootstrap import bootstrap_runtime_observability

    bootstrap_runtime_observability("scheduler")

    async def _run() -> None:
        from services import (
            aclose_app_services,
            get_scheduler_runner,
            init_app_services,
        )

        await init_app_services()
        try:
            runner = await get_scheduler_runner()
            await runner.run()
        finally:
            await aclose_app_services()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
