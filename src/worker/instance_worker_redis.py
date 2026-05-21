from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

from analysis.login_ads import login_ad_task_types
from config.paths import repo_root
from config.redis_health import ping_async_redis_or_exit
from navigation.lifecycle_states import InstanceState
from scheduler.claims import CooperativeClaims
from scheduler.queue import QueueItem, RedisQueue

logger = logging.getLogger(__name__)

_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"

def _login_ad_task_types() -> frozenset[str]:
    """Overlay-pushed login ads (see ``analysis.login_ads.login_ad_task_types``)."""
    return login_ad_task_types(repo_root())


# Login popups often appear a few seconds after main_city is visible (case 2/4).
_WHO_I_AM_BOOT_GRACE_S = 8.0
# After a login-ad scenario finishes, wait before identity so a follow-up popup
# can be overlay-pushed and queued (stacked banners on main_city).
_LOGIN_AD_SETTLE_S = 2.0



if TYPE_CHECKING:
    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerRedisMixin(_Base):
    _cfg: Any
    _redis: aioredis.Redis | None
    _queue: RedisQueue | None
    _claims: Any
    _instance_state: InstanceState
    _task_registry: dict[str, type]

    async def _connect(self) -> None:
        from config.redis_metrics import instrument_redis_client

        settings = self._settings
        if self._redis is None:
            url = settings.redis.url
            self._redis = aioredis.from_url(
                url,
                socket_connect_timeout=5.0,
            )
            instrument_redis_client(self._redis, component="worker")
            await ping_async_redis_or_exit(self._redis, url=url)
        if self._queue is None:
            self._queue = RedisQueue(self._redis, settings)
        self._claims = CooperativeClaims(self._redis)

        inst_key = _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id)
        # ``current_task_*`` / ``current_scenario`` / ``last_overlay_match_*``
        # are deliberately NOT reset here — they're the only breadcrumbs left
        # of a task that was in flight when the previous worker died, and
        # ``_fail_stuck_running_on_boot`` reads them to write a history entry
        # when ``wos:queue:running:<iid>`` has already TTL'd away (restart
        # more than 180s after the crash). The boot cleanup wipes them after.
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
                "nav_target": "",
                "current_screen": "",
            },
        )

    async def _disconnect_redis(self) -> None:
        """Drain async Redis connections before the supervisor event loop stops."""
        client = self._redis
        self._redis = None
        self._queue = None
        self._claims = None
        if client is None or not self._owns_redis:
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
            if item is not None and self._redis is not None:
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
        if str(current_screen or "").strip().lower() == "loading":
            return f"{len(rows)} due item(s) blocked: current_screen is loading"
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
                f"{data.get('task_type') or '?'!s}[{data.get('player_id') or 'device'!s}]"
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

            _pids_fn = getattr(instance_worker, "player_ids_for_device_candidates", None)
            if callable(_pids_fn):
                _cfg_pids = _pids_fn(self._cfg.bluestacks_window_title, self._cfg.instance_id)
            else:
                _cfg_pids = instance_worker.player_ids_for_device(self._cfg.bluestacks_window_title)
            resolved = (active or (_cfg_pids[0] if _cfg_pids else "")).strip()
            if not resolved:
                return item

        # ``replace`` preserves all other fields verbatim — notably
        # ``created_at`` (tie-breaker for ranking) and ``effective_priority``
        # (carried through to DslScenarioTask for preemption comparisons in
        # ``instance_worker.py``). Hand-listed field copies dropped these
        # silently because their dataclass defaults (0.0 / 0) made the bug
        # invisible until a high-priority overlay tried to preempt a resolved
        # device-level task and lost the comparison.
        return dataclasses.replace(item, player_id=resolved)

    async def _ensure_account(self, player_id: str) -> None:
        # An empty id here means the caller could not resolve identity (no
        # active_player in Redis, no device→pid mapping). Writing "" would
        # wipe the previously-identified player from the instance state and
        # desync identity until the next who_i_am probe re-bootstraps it.
        if self._redis is None or not str(player_id or "").strip():
            return
        await self._redis.hset(
            _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
            "active_player",
            player_id,
        )

    def _note_boot_interactive_screen(self, screen: str) -> None:
        """Start the post-load boot grace window once UI leaves ``loading``."""
        s = str(screen or "").strip().lower()
        if not s or s == "loading":
            return
        if float(getattr(self, "_boot_interactive_at", 0.0) or 0.0) <= 0.0:
            self._boot_interactive_at = time.monotonic()

    async def _instance_current_screen(self) -> str:
        r = self._redis
        if r is None:
            return str(getattr(self, "_last_current_screen", None) or "").strip()
        try:
            raw = await r.hget(
                _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
                "current_screen",
            )
        except Exception:
            return str(getattr(self, "_last_current_screen", None) or "").strip()
        return (raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip()

    def _boot_ready_for_who_i_am_enqueue(self) -> bool:
        """Whether phase-2 identity may be queued (post-load grace + post-ad settle)."""
        interactive_at = float(getattr(self, "_boot_interactive_at", 0.0) or 0.0)
        if interactive_at <= 0.0:
            return False
        last_ad_done = float(getattr(self, "_last_login_ad_finished_at", 0.0) or 0.0)
        if last_ad_done > 0.0:
            return time.monotonic() >= last_ad_done + _LOGIN_AD_SETTLE_S
        return time.monotonic() - interactive_at >= _WHO_I_AM_BOOT_GRACE_S

    def note_login_ad_task_finished(self, task_type: str) -> None:
        if str(task_type or "").strip() in _login_ad_task_types():
            self._last_login_ad_finished_at = time.monotonic()

    async def _login_ads_phase_active(self) -> bool:
        """True while a login-ad scenario is running or still pending in the queue."""
        r = self._redis
        if r is None:
            return False
        inst = self._cfg.instance_id
        running_key = f"wos:queue:running:{inst}"
        try:
            raw_run = await r.get(running_key)
        except Exception:
            raw_run = None
        if raw_run:
            try:
                pl = json.loads(
                    raw_run.decode() if isinstance(raw_run, bytes) else str(raw_run)
                )
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                pl = {}
            if str(pl.get("task_type") or "").strip() in _login_ad_task_types():
                return True
        try:
            raw_cs = await r.hget(
                _INST_STATE_KEY_FMT.format(instance_id=inst), "current_scenario"
            )
            cs = (raw_cs.decode() if isinstance(raw_cs, bytes) else str(raw_cs or "")).strip()
        except Exception:
            cs = ""
        if cs in _login_ad_task_types():
            return True
        q = self._queue
        if q is None:
            return False
        try:
            from scheduler.queue import _queue_key

            key = _queue_key(inst)
            raw_items = await r.zrangebyscore(key, "-inf", "+inf")
        except Exception:
            logger.debug("login ads phase: queue scan failed instance=%s", inst, exc_info=True)
            return False
        for raw in raw_items:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                continue
            if str(data.get("task_type") or "").strip() in _login_ad_task_types():
                return True
        return False

    async def _maybe_enqueue_who_i_am_when_active_player_missing(self) -> None:
        """Enqueue ``who_i_am`` when ``active_player`` is empty and login ads have drained.

        Phase 1: overlay pushes per-ad scenarios (each with its own node). Phase 2:
        this runs only when no login-ad task is running or queued. Also covers cleared
        Redis state, failed probes, or manual wipes without restarting the worker.
        """
        if getattr(self, "_stopping", False) or getattr(self, "_ui_paused", False):
            return
        q = self._queue
        r = self._redis
        if q is None or r is None:
            return

        if await self._login_ads_phase_active():
            logger.debug(
                "identity probe: deferred — login ads phase active instance=%s",
                self._cfg.instance_id,
            )
            return

        if (await self._instance_current_screen()).lower() == "loading":
            logger.debug(
                "identity probe: deferred — game still loading instance=%s",
                self._cfg.instance_id,
            )
            return

        if not self._boot_ready_for_who_i_am_enqueue():
            logger.debug(
                "identity probe: deferred — boot grace/settle instance=%s",
                self._cfg.instance_id,
            )
            return

        inst = self._cfg.instance_id
        inst_key = _INST_STATE_KEY_FMT.format(instance_id=inst)
        try:
            raw_ap = await r.hget(inst_key, "active_player")
        except Exception:
            logger.debug(
                "identity probe: active_player read failed instance=%s",
                inst,
                exc_info=True,
            )
            return
        ap = (raw_ap.decode() if isinstance(raw_ap, bytes) else str(raw_ap or "")).strip()
        if ap:
            return

        running_key = f"wos:queue:running:{inst}"
        try:
            raw_run = await r.get(running_key)
        except Exception:
            raw_run = None
        if raw_run:
            try:
                pl = json.loads(
                    raw_run.decode() if isinstance(raw_run, bytes) else str(raw_run)
                )
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                pl = {}
            if str(pl.get("task_type") or "").strip() == "who_i_am":
                return

        try:
            raw_cs = await r.hget(inst_key, "current_scenario")
            cs = (raw_cs.decode() if isinstance(raw_cs, bytes) else str(raw_cs or "")).strip()
        except Exception:
            cs = ""
        if cs == "who_i_am":
            return

        root = repo_root()
        try:
            from dsl.dsl_schema import dsl_scenario_yaml_priority

            prio = int(dsl_scenario_yaml_priority(root, "who_i_am") or 82_000)
        except Exception:
            prio = 82_000

        run_at = time.time()
        task_id = f"identity:{inst}:who_i_am:{uuid.uuid4().hex[:8]}"
        try:
            ok = await q.schedule(
                task_id=task_id,
                player_id="",
                task_type="who_i_am",
                priority=prio,
                run_at=run_at,
                instance_id=inst,
                skip_if_duplicate=True,
                dedup_ignore_region=True,
            )
        except Exception:
            logger.exception(
                "identity probe: enqueue who_i_am failed instance=%s",
                inst,
            )
            return
        if ok:
            logger.info(
                "identity probe: enqueued who_i_am (active_player empty) instance=%s",
                inst,
            )
