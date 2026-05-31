"""Top-level ``execute`` pipeline for :class:`tasks.dsl_scenario.DslScenarioTask`."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from adb.frame_normalize import GAME_FRAME_SIZE
from config.log_ansi import scenario_log_label as _scen
from dashboard.notifications import push_ui_notification
from dsl import template_resolver as _tmpl
from layout.area_lookup import screen_region_by_name
from tasks.base import TaskResult
from tasks.dsl_scenario_helpers import (
    _DSL_STEP_ACTION_KEYS,
    _action_pause_seconds,
    _BreakRepeat,
    _collect_ocr_store_targets,
    _dsl_cond_allows_step,
    _enqueue_scenario,
    _jittered_wait_seconds,
    _load_area_json,
    _ocr_store_redis_fields,
    _parse_wait_seconds,
    _read_active_player,
    _read_current_screen,
    _resolve_push_delay_seconds,
)

logger = logging.getLogger(__name__)

# TYPE_CHECKING-only base: gives ty visibility into every host attribute and
# sibling-mixin method without changing the runtime MRO of DslScenarioTask.
# See ``tasks/_dsl_task_host.py`` for the rationale.
if TYPE_CHECKING:
    from tasks._dsl_task_host import _DslTaskHost as _Base
else:
    _Base = object


class DslScenarioExecuteMixin(_Base):
    """Main scenario YAML runner (load doc → navigate → step loop)."""

    async def execute(self, instance_id: str) -> TaskResult:
        key = str(self.scenario_key or "").strip()
        if not key:
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "missing_scenario_key"},
            )

        # Wipe the previous scenario's `dsl_last_*` audit snapshot so the
        # click-approvals UI doesn't show stale guard outcomes during the
        # window between scenario start and our first match/ocr/color step.
        await self._reset_dsl_audit_state(instance_id)

        # Resolve helpers via the ``tasks.dsl_scenario`` re-exporter rather
        # than the module-local imports so tests can keep monkeypatching
        # symbols like ``_repo_root`` and ``BotActions`` on ``dsl_scenario``
        # (the historical patch site) without knowing about the mixin split.
        from tasks import dsl_scenario as _dsl_proxy

        repo_root = _dsl_proxy._repo_root()

        # Resolve scenario by key across module-owned scenario roots. Literal
        # ``{key}.yaml`` wins; template files like ``level_up_{hero}.yaml`` can
        # match the key and substitute placeholders before YAML parse.
        loaded = _tmpl.load_doc(repo_root, key)
        if loaded is None:
            await push_ui_notification(
                self.redis_client,
                instance_id,
                kind="dsl.scenario_not_found",
                message=f"Scenario not found: {key}",
                level="error",
                event_id=f"dsl:scenario_not_found:{instance_id}:{key}",
                payload={"scenario": key},
            )
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "scenario_not_found", "key": key},
            )
        path, doc = loaded
        steps = doc.get("steps")
        if not isinstance(steps, list):
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "invalid_steps", "path": str(path)},
            )
        # Pre-flight gate: walk the step tree and reject scenarios with
        # well-defined typos (e.g. ``ocr: ... scope: instnace``) before any
        # tap fires. The runtime used to fall back to ``scope=player`` with
        # a warning and continue — silently writing to the wrong key and
        # leaving the cleanup walk targeting the wrong scope on the next
        # boot. See ``scenarios.dsl_schema.validate_dsl_steps``.
        from dsl.dsl_schema import validate_dsl_steps

        validation_errors = validate_dsl_steps(steps)
        if validation_errors:
            logger.error(
                "dsl_scenario: %s rejected at start with %d validation error(s): %s",
                _scen(key),
                len(validation_errors),
                "; ".join(validation_errors),
            )
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={
                    "scenario": key,
                    "reason": "scenario_invalid",
                    "errors": validation_errors,
                    "path": str(path),
                },
            )
        steps_total_n = len(steps)
        steps_trace: list[dict[str, Any]] = []
        # Expose to nested handlers in ``DslScenarioInlineMixin._run_inline_step``
        # so they can append rows for clicks, waits, and per-iteration markers
        # inside ``while_match`` / ``repeat`` containers.
        self._steps_trace = steps_trace
        # Seed scenario-relative timing for ``_append_trace_row`` — every row
        # gets a ``t`` (seconds from scenario start) and terminal top-level
        # rows additionally get ``duration_ms``. See ``DslPersistMixin``.
        self._scenario_started_at = time.time()
        self._step_start_times = {}

        def _trace_row(i: Any, step_obj: Any, status: str, **kw: Any) -> None:
            self._append_trace_row(i, step_obj, status, **kw)

        def _fin(meta: dict[str, Any], *, completed: bool) -> dict[str, Any]:
            m = dict(meta)
            m["steps_trace"] = list(steps_trace)
            m["steps_total"] = steps_total_n
            m["scenario_completed"] = completed
            if self.start_step_index:
                m["resume_from_step_index"] = int(self.start_step_index)
            return m

        async def _mark_top_level_step_done() -> None:
            """After a top-level step finishes, point the UI at the next step index."""
            await self._publish_scenario_step_index(
                instance_id,
                min(step_index, max(steps_total_n - 1, 0)),
            )

        # Hydrate ``steps_trace`` from the previous slice when resuming.
        # Without this, the trace in each TaskResult only reflects the current
        # invocation — preempt → resume splits the history across two records.
        # The companion write happens on the preempt-yield return below; final
        # exits clear the field via ``_clear_step_context``.
        if self.start_step_index > 0 and self.redis_client is not None:
            with suppress(Exception):
                raw_prior = await self.redis_client.hget(
                    f"wos:instance:{instance_id}:state",
                    "last_active_scenario_trace",
                )
                if raw_prior:
                    try:
                        prior = json.loads(raw_prior)
                    except (ValueError, TypeError):
                        prior = None
                    if isinstance(prior, list):
                        steps_trace.extend(
                            x for x in prior if isinstance(x, dict)
                        )

        # Sentinel -1 = "seed read failed, re-seed lazily on first probe".
        # See ``_preempted_by_new_debug`` — distinguishing a transient Redis
        # error from a true 0 prevents spurious dsl_preempted_debug on every
        # subsequent step when the live key is > 0.
        seed = await self._read_dsl_preempt_gen(instance_id)
        self._preempt_gen_at_start = -1 if seed is None else seed

        raw_root_cond = doc.get("cond")
        if raw_root_cond is not None and not isinstance(raw_root_cond, bool):
            cond_s = str(raw_root_cond).strip()
            if cond_s and not await _dsl_cond_allows_step(
                {"cond": raw_root_cond},
                instance_id,
                self.redis_client,
                state_flat=self._state_flat(),
            ):
                await self._clear_step_context(instance_id)
                logger.debug(
                    "dsl_scenario: scenario skipped by root cond (%s)", cond_s
                )
                _trace_row(
                    0,
                    {"cond": raw_root_cond},
                    "early_exit",
                    reason="scenario_cond_false",
                    cond=cond_s,
                )
                return TaskResult(
                    success=True,
                    next_run_at=None,
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "scenario_cond_false",
                            "cond": cond_s,
                        },
                        completed=True,
                    ),
                )

        if await self._preempted_by_new_debug(instance_id):
            await self._clear_step_context(instance_id)
            _trace_row(
                0,
                {"navigate_to": ""},
                "early_exit",
                reason="dsl_preempted_debug",
            )
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata=_fin(
                    {
                        "scenario": key,
                        "reason": "dsl_preempted_debug",
                        "preempted": True,
                    },
                    completed=False,
                ),
            )

        from tasks import dsl_runtime

        actions = dsl_runtime.bot_actions()
        area_doc = _load_area_json(repo_root)
        dev_w, dev_h = GAME_FRAME_SIZE

        # Wipe ephemeral ``ocr: store: ...`` Redis fields from any previous
        # run of this scenario before stepping in. ``store:`` is documented
        # as scenario-step scoped (memory ``feedback_dsl_state_vs_store``),
        # but until this hook nothing actually enforced it — values lingered
        # between runs, causing e.g. ``squad_fight``'s loop ``cond`` to
        # short-circuit on a 21h-old ``"Defeat!"`` from the prior fight.
        # Only fire on a *fresh* start (``start_step_index <= 0``); resumed
        # tasks must preserve whatever earlier steps already wrote.
        if self.start_step_index <= 0 and self.redis_client is not None:
            store_targets = _collect_ocr_store_targets(steps)
            if store_targets:
                player_fields: list[str] = []
                instance_fields: list[str] = []
                for scope, field in store_targets:
                    siblings = _ocr_store_redis_fields(field)
                    (player_fields if scope == "player" else instance_fields).extend(siblings)
                if player_fields and self.player_id:
                    with suppress(Exception):
                        await self.redis_client.hdel(
                            f"wos:player:{self.player_id}:state", *player_fields
                        )
                if instance_fields:
                    with suppress(Exception):
                        await self.redis_client.hdel(
                            f"wos:instance:{instance_id}:state", *instance_fields
                        )

        if await self._preempted_by_new_debug(instance_id):
            await self._clear_step_context(instance_id)
            _trace_row(
                0,
                {"navigate_to": ""},
                "early_exit",
                reason="dsl_preempted_debug",
            )
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata=_fin(
                    {
                        "scenario": key,
                        "reason": "dsl_preempted_debug",
                        "preempted": True,
                    },
                    completed=False,
                ),
            )

        # Optional root-level `node:` / `nodes:` — restrict the FSM screens
        # where this scenario is allowed to run. Single string and list forms
        # are both accepted (see ``scenarios.dsl_schema.scenario_allowed_nodes``).
        # If ``current_screen`` is already in the set, navigation is skipped —
        # the scenario runs in place. Otherwise the FSM is driven to the FIRST
        # entry. Use this when the same scenario applies to many sibling
        # screens (e.g. ``shop.tab.advance`` works from any shop sub-page).
        from dsl.dsl_schema import scenario_allowed_nodes

        allowed_nodes = scenario_allowed_nodes(doc)
        target_node = allowed_nodes[0] if allowed_nodes else ""
        # `device_level: true` opts a scenario out of identity gating (see
        # `RedisQueue.pop_due`).  Reused here as the default mode for `while_match`:
        # device-level scenarios (popup dismissals, identity probes) keep the
        # legacy "0 iterations = success" semantics, since their triggers may
        # legitimately have already been resolved.  Player-bound scenarios get
        # initial-probe retries + strict zero-iteration failure so the work
        # actually happens (or is properly retried).
        is_device_level = doc.get("device_level") is True

        # Implicit player-identity gate. Player-bound scenarios (everything
        # without ``device_level: true``) need a non-empty ``player_id`` on
        # the queue item. Overlay pushes that fire before ``who_i_am`` has
        # written ``active_player`` to instance state produce tasks with
        # ``player_id=""``; without this gate they used to limp along, and
        # every Redis-touching helper (``_resolve_player_id``, ``ocr:
        # store:``, player-state cond reads) had to keep its own "if pid is
        # empty, fall back to active_player" branch. Gating here lets those
        # call-sites assume a non-empty ``player_id`` downstream.
        #
        # Skip is benign — same shape as ``scenario_cond_false`` so cron /
        # overlay re-enqueue picks the work up on the next tick (by then
        # ``who_i_am`` will have run and the next push will carry a real
        # ``player_id``).
        if (
            not is_device_level
            and not str(self.player_id or "").strip()
            and self.start_step_index <= 0
        ):
            await self._clear_step_context(instance_id)
            logger.info(
                "dsl_scenario: skipping %s — awaiting player identity "
                "(task carries empty player_id; who_i_am hasn't run)",
                _scen(key),
            )
            _trace_row(
                0,
                {"player_id": ""},
                "early_exit",
                reason="awaiting_player_identity",
            )
            return TaskResult(
                success=True,
                next_run_at=None,
                metadata=_fin(
                    {
                        "scenario": key,
                        "reason": "awaiting_player_identity",
                    },
                    completed=True,
                ),
            )

        # Pre-flight screen identity gate. When a scenario declares
        # ``node:`` / ``nodes:`` it expects ``current_screen`` to be *known* on
        # entry. If identity is temporarily blank, do not burn the queued
        # scenario: yield it back with a short retry so the rolling detector can
        # restore the node. This applies to ``device_level: true`` scenarios as
        # well when they declare ``node:`` — navigation cannot route from an
        # unknown source, and burning the attempt only feeds a hot retry loop
        # (e.g. ``who_i_am`` re-enqueued every rolling tick).
        cur_screen_at_entry = ""
        if (
            target_node
            and self.start_step_index <= 0
            and self.redis_client is not None
        ):
            cur_screen_at_entry = await _read_current_screen(
                instance_id, self.redis_client
            )
            if not cur_screen_at_entry:
                _trace_row(
                    0,
                    {"navigate_to": target_node},
                    "early_exit",
                    reason="awaiting_screen_identity",
                    target=target_node,
                )
                logger.info(
                    "dsl_scenario: deferring %s — current_screen is empty on %s",
                    _scen(key), instance_id,
                )
                return TaskResult(
                    success=False,
                    next_run_at=datetime.now(tz=UTC) + timedelta(seconds=5),
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "awaiting_screen_identity",
                            "target_node": target_node,
                        },
                        completed=False,
                    ),
                )

        if target_node and self.start_step_index <= 0:
            # Multi-node scenarios: if we're already on one of the allowed
            # nodes, skip navigation entirely — the steps below run in place.
            # ``shop.tab.advance`` (``nodes: [shop, shop.daily_deals, ...]``)
            # is the motivating case: from any shop sub-page the next-page
            # arrow is already on screen, so a round-trip to the hub is
            # pure waste.
            nav_started_at = 0.0  # only consulted in the ``not nav_ok`` branch
            if cur_screen_at_entry and cur_screen_at_entry in allowed_nodes:
                nav_ok = True
            else:
                nav_started_at = time.time()
                nav_ok = await self._navigate_to_node(
                    instance_id,
                    target_node,
                    actions=actions,
                    scenario_key=key,
                )
            if nav_ok and self.redis_client is not None:
                with suppress(Exception):
                    await self.redis_client.hset(
                        f"wos:instance:{instance_id}:state", "nav_error", ""
                    )
            if not nav_ok:
                # Capture current_screen BEFORE _clear_step_context wipes it
                # (the clear here also blanks the screen field via the navigation
                # error mapping below). Keep this row in the trace so the task
                # record explains *why* the scenario aborted — without it,
                # navigation_failed returns produce an empty steps_trace and
                # the UI shows "0 steps ran" with no hint of what happened.
                cur_at_fail = ""
                rejected_by_operator = False
                if self.redis_client is not None:
                    with suppress(Exception):
                        raw_cs = await self.redis_client.hget(
                            f"wos:instance:{instance_id}:state",
                            "current_screen",
                        )
                        cur_at_fail = (
                            raw_cs.decode()
                            if isinstance(raw_cs, bytes)
                            else str(raw_cs or "")
                        ).strip()
                    # Distinguish operator reject from real nav failure. The
                    # approval gate stamps ``last_approval_reject_at`` when the
                    # operator presses Reject; if that timestamp lands inside
                    # this nav attempt, no tap fired and ``current_screen`` is
                    # still valid — leave it alone.
                    with suppress(Exception):
                        raw_rej = await self.redis_client.hget(
                            f"wos:instance:{instance_id}:state",
                            "last_approval_reject_at",
                        )
                        rej_s = (
                            raw_rej.decode()
                            if isinstance(raw_rej, bytes)
                            else str(raw_rej or "")
                        ).strip()
                        if rej_s:
                            try:
                                rejected_by_operator = float(rej_s) >= nav_started_at
                            except ValueError:
                                rejected_by_operator = False
                await self._clear_step_context(instance_id)
                if self.redis_client is not None:
                    with suppress(Exception):
                        from_nav = cur_at_fail or "(blank — detector/verify may not have written Redis yet)"
                        if rejected_by_operator:
                            nav_msg = (
                                f"navigation_aborted: {from_nav} → {target_node} "
                                f"(scenario {key}; operator rejected approval)"
                            )
                            mapping: dict[str, str] = {
                                "nav_error": nav_msg,
                                "last_approval_reject_at": "",
                            }
                        else:
                            nav_msg = (
                                f"navigation_failed: {from_nav} → {target_node} "
                                f"(scenario {key}; no route, verify failed after tap, or tap blocked)"
                            )
                            mapping = {
                                "nav_error": nav_msg,
                                "current_screen": "",
                            }
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            mapping=mapping,
                        )
                _trace_row(
                    0,
                    {"navigate_to": target_node},
                    "early_exit",
                    reason="navigation_failed",
                    target=target_node,
                    current_screen=cur_at_fail,
                )
                return TaskResult(
                    success=False,
                    # No baked-in retry: the task drops from queue and re-pushes
                    # come from natural channels — overlay tick (if the trigger
                    # is still on screen), cron (next scheduled fire), or the
                    # identity-probe re-enqueue (``who_i_am`` specifically, via
                    # ``_maybe_enqueue_who_i_am_when_active_player_missing``).
                    # The previous 5-minute hard backoff was deadweight for
                    # bootstrap scenarios — a stuck ``who_i_am`` blocked every
                    # player-bound task for 5 min after each nav miss.
                    next_run_at=None,
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "navigation_failed",
                            "target_node": target_node,
                        },
                        completed=False,
                    ),
                )

        step_index = self.start_step_index
        require_identity_resolution = key == "who_i_am" and not str(self.player_id or "").strip()
        while step_index < len(steps):
            step = steps[step_index]
            _resumable_step = step_index  # capture before increment for resume tracking
            step_index += 1
            if await self._preempted_by_new_debug(instance_id):
                await self._clear_step_context(instance_id)
                _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                return TaskResult(
                    success=False,
                    next_run_at=None,
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "dsl_preempted_debug",
                            "preempted": True,
                        },
                        completed=False,
                    ),
                )
            preempt_yield = await self._preempted_by_higher_priority(
                instance_id, _resumable_step
            )
            if preempt_yield is not None:
                await self._clear_step_context(instance_id)
                _trace_row(
                    _resumable_step,
                    step,
                    "preempted",
                    reason="preempted_by_higher_priority",
                )
                # Persist accumulated trace so the resumed slice can hydrate
                # the full history into its TaskResult. Written AFTER
                # ``_clear_step_context`` (which blanks the field) so the row
                # survives.
                if self.redis_client is not None:
                    with suppress(Exception):
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            "last_active_scenario_trace",
                            json.dumps(steps_trace),
                        )
                md = dict(preempt_yield.metadata or {})
                # Always resume at the actual yielded step. Previously this
                # reset to 0 when ``target_node`` was set, which forced the
                # resumed slice back through the root-node navigation gate —
                # and when a mid-scenario popup made ``current_screen`` no
                # longer match the target, the BFS failed and the scenario
                # died with ``navigation_failed`` even though it had already
                # entered the target screen earlier.
                md["resume_from_step_index"] = int(_resumable_step)
                return TaskResult(
                    success=preempt_yield.success,
                    next_run_at=preempt_yield.next_run_at,
                    metadata=_fin(md, completed=False),
                )
            # Persist current step so hand-pointer resume and the UI progress bar
            # stay in sync (also refreshed after each step completes).
            await self._publish_scenario_step_index(instance_id, _resumable_step)
            if not isinstance(step, dict):
                _trace_row(_resumable_step, step, "skipped_invalid")
                continue
            if not await _dsl_cond_allows_step(
                step,
                instance_id,
                self.redis_client,
                state_flat=self._state_flat(),
            ):
                logger.debug("dsl_scenario: step skipped by cond (%s)", step.get("cond"))
                _trace_row(_resumable_step, step, "skipped_cond")
                continue
            grouped = step.get("steps")
            if (
                isinstance(grouped, list)
                and grouped
                and not _DSL_STEP_ACTION_KEYS.intersection(step.keys())
            ):
                await self._write_step_context(instance_id, scenario=key)
                for inner_idx, raw_inner in enumerate(grouped):
                    if not isinstance(raw_inner, dict):
                        continue
                    inner: dict[str, Any] = cast("dict[str, Any]", raw_inner)
                    if not await _dsl_cond_allows_step(
                        inner,
                        instance_id,
                        self.redis_client,
                        state_flat=self._state_flat(),
                    ):
                        logger.debug(
                            "dsl_scenario: grouped step skipped by cond (%s)",
                            inner.get("cond"),
                        )
                        continue
                    result = await self._run_inline_step(
                        inner,
                        actions=actions,
                        area_doc=area_doc,
                        repo_root=repo_root,
                        instance_id=instance_id,
                        dev_w=dev_w,
                        dev_h=dev_h,
                        scenario_key=key,
                        trace_path=f"{_resumable_step}.{inner_idx}",
                    )
                    if result is not None:
                        md = dict(result.metadata or {})
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason=str(md.get("reason") or ""),
                        )
                        return TaskResult(
                            success=result.success,
                            next_run_at=result.next_run_at,
                            metadata=_fin(md, completed=False),
                        )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
            if "match" in step:
                reg = str(step.get("match") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                row = await self._match_region(
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    scenario_key=key,
                    step=step,
                    region=reg,
                )
                # ``match + steps`` = guarded block: matched → run ``steps``,
                # miss → run ``else`` (if any) and continue. The presence of
                # ``steps:`` is the explicit opt-in to soft semantics; bare
                # ``match:`` keeps its historical hard-gate behavior (abort
                # the scenario on miss) so existing gate-style usages stand.
                inner_steps = step.get("steps")
                else_steps = step.get("else")
                has_guarded_block = (
                    isinstance(inner_steps, list) and bool(inner_steps)
                ) or (isinstance(else_steps, list) and bool(else_steps))
                if has_guarded_block:
                    matched = bool(row.get("matched")) if row else False
                    branch_steps = inner_steps if matched else else_steps
                    branch_label = "steps" if matched else "else"
                    if isinstance(branch_steps, list) and branch_steps:
                        for inner_idx, inner in enumerate(branch_steps):
                            if not isinstance(inner, dict):
                                continue
                            result = await self._run_inline_step(
                                inner,
                                actions=actions,
                                area_doc=area_doc,
                                repo_root=repo_root,
                                instance_id=instance_id,
                                dev_w=dev_w,
                                dev_h=dev_h,
                                scenario_key=key,
                                trace_path=(
                                    f"{_resumable_step}.{branch_label}.{inner_idx}"
                                ),
                            )
                            if result is not None:
                                md = dict(result.metadata or {})
                                _trace_row(
                                    _resumable_step,
                                    step,
                                    "stopped",
                                    reason=str(md.get("reason") or ""),
                                )
                                return TaskResult(
                                    success=result.success,
                                    next_run_at=result.next_run_at,
                                    metadata=_fin(md, completed=False),
                                )
                    await _mark_top_level_step_done()
                    _trace_row(
                        _resumable_step,
                        step,
                        "ok",
                        matched=matched,
                        branch=branch_label,
                    )
                    continue
                if row is None:
                    await self._clear_step_context(instance_id)
                    _trace_row(_resumable_step, step, "early_exit", reason="match_region_not_found")
                    return TaskResult(
                        success=True,
                        next_run_at=None,
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "match_region_not_found",
                                "region": reg,
                            },
                            completed=False,
                        ),
                    )
                matched = bool(row.get("matched"))
                if not matched:
                    logger.info(
                        "dsl_scenario: match guard failed — skipping scenario %s region=%s row=%s",
                        _scen(key),
                        reg,
                        row,
                    )
                    await self._clear_step_context(instance_id)
                    _trace_row(_resumable_step, step, "early_exit", reason="match_guard_failed")
                    # ``match_guard_failed`` is a *failure* of intent: the
                    # scenario declared "I need this region present" and it
                    # wasn't. Marking the task ``success=True`` lumped these
                    # rows together with real completions in queue history
                    # (e.g. ``new_chapter`` shows ``reason=match_guard_failed``
                    # but reads as success). Report it honestly so the UI
                    # surfaces it as a failure; ``scenario_completed=False``
                    # in metadata stays as the structural marker.
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "match_guard_failed",
                                "region": reg,
                                "match": row if isinstance(row, dict) else None,
                            },
                            completed=False,
                        ),
                    )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
            if "while_match" in step:
                reg = str(step.get("while_match") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                try:
                    max_iters = int(step.get("max", 20))
                except (TypeError, ValueError):
                    max_iters = 20
                max_iters = max(0, max_iters)
                inner_steps = step.get("steps")
                if not isinstance(inner_steps, list):
                    inner_steps = []

                # The *initial* probe may retry (default 1 attempt; opt in via
                # ``retry.attempts``) to absorb screen-settling lag after navigation.
                # Subsequent probes are single-shot — once we've matched once,
                # lack of a match means the work is done.
                #
                # YAML form:
                #   retry:
                #     attempts: 3
                #     interval: 500ms     # also accepts "0.5s" or raw seconds
                default_attempts = 1
                default_interval_s = 0.5
                # Scenario ``steps:`` are OR-semantics: each step tries; if a
                # ``while_match`` finds zero iterations, we just move to the
                # next step instead of failing the whole scenario. The previous
                # strict-by-default behavior for player-bound scenarios meant a
                # missing claim button (popup already closed, wrong screen) would
                # abort the scenario and pop an approval prompt — but the natural
                # idiom across our scenarios (``claim_trials``, ``mail.claim``,
                # ``vip_rewards``, etc.) is "try claim X, then claim Y, then …";
                # failure of one branch should not prevent the rest from running.
                # YAML can still set ``strict: true`` to opt into the
                # "must have done work" check for the rare gate-like step.
                default_strict = False
                retry_cfg = step.get("retry")
                if not isinstance(retry_cfg, dict):
                    retry_cfg = {}
                try:
                    initial_attempts = int(retry_cfg.get("attempts", default_attempts))
                except (TypeError, ValueError):
                    initial_attempts = default_attempts
                initial_attempts = max(1, initial_attempts)
                if "interval" in retry_cfg:
                    attempt_interval_s = _parse_wait_seconds(retry_cfg.get("interval"))
                else:
                    attempt_interval_s = default_interval_s
                attempt_interval_s = max(0.0, attempt_interval_s)
                strict = bool(step.get("strict", default_strict))

                iterations = 0
                inner_result: TaskResult | None = None
                for _ in range(max_iters):
                    if await self._preempted_by_new_debug(instance_id):
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "dsl_preempted_debug",
                                    "preempted": True,
                                },
                                completed=False,
                            ),
                        )
                    probe_attempts = initial_attempts if iterations == 0 else 1
                    matched = False
                    for attempt in range(probe_attempts):
                        # Force a fresh capture on each retry — _match_region reads
                        # capture_screen_bgr_cached, so without invalidation every
                        # attempt probes the same frame and the retry is a no-op.
                        if attempt > 0 and hasattr(actions, "invalidate_frame_cache"):
                            with suppress(Exception):
                                actions.invalidate_frame_cache(instance_id)
                        row = await self._match_region(
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            scenario_key=key,
                            step=step,
                            region=reg,
                        )
                        if row is not None and bool(row.get("matched")):
                            matched = True
                            break
                        if attempt < probe_attempts - 1:
                            await asyncio.sleep(attempt_interval_s)
                    if not matched:
                        break
                    iter_path = f"{_resumable_step}.{iterations}"
                    self._append_trace_row(
                        iter_path, None, "iter", summary=f"iter {iterations}"
                    )
                    for inner_idx, inner in enumerate(inner_steps):
                        if not isinstance(inner, dict):
                            continue
                        result = await self._run_inline_step(
                            inner,
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            trace_path=f"{iter_path}.{inner_idx}",
                        )
                        if result is not None:
                            inner_result = result
                            break
                    if inner_result is not None:
                        break
                    iterations += 1
                    await self._publish_scenario_step_index(
                        instance_id, _resumable_step, loop_iter=iterations
                    )

                if inner_result is not None:
                    md = dict(inner_result.metadata or {})
                    _trace_row(
                        _resumable_step,
                        step,
                        "stopped",
                        reason=str(md.get("reason") or ""),
                    )
                    return TaskResult(
                        success=inner_result.success,
                        next_run_at=inner_result.next_run_at,
                        metadata=_fin(md, completed=False),
                    )

                # ``else:`` — explicit fallback for the no-iterations case.
                # When provided, it bypasses the strict-reschedule path: the
                # scenario has declared how it wants to handle "icon never
                # appeared" itself (e.g. set a TTL, push another scenario).
                else_steps = step.get("else")
                if (
                    iterations == 0
                    and isinstance(else_steps, list)
                    and else_steps
                ):
                    else_result: TaskResult | None = None
                    for else_idx, else_step in enumerate(else_steps):
                        if not isinstance(else_step, dict):
                            continue
                        else_result = await self._run_inline_step(
                            else_step,
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            trace_path=f"{_resumable_step}.else.{else_idx}",
                        )
                        if else_result is not None:
                            break
                    if else_result is not None:
                        md = dict(else_result.metadata or {})
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason=str(md.get("reason") or "else_stop"),
                        )
                        return TaskResult(
                            success=else_result.success,
                            next_run_at=else_result.next_run_at,
                            metadata=_fin(md, completed=False),
                        )
                    logger.info(
                        "dsl_scenario: while_match else-branch ran scenario=%s region=%s",
                        _scen(key),
                        reg,
                    )
                    await _mark_top_level_step_done()
                    _trace_row(_resumable_step, step, "ok", iterations=0, branch="else")
                    continue

                if iterations == 0 and strict:
                    # Strict mode: zero iterations after initial-probe retries
                    # means the work didn't happen.  Reschedule so the next
                    # `pop_due` cycle gets another shot instead of yielding to
                    # whatever lower-priority task is in the queue.
                    approved = await self._pause_for_while_match_no_iterations_approval(
                        actions=actions,
                        instance_id=instance_id,
                        scenario_key=key,
                        region=reg,
                        attempts=initial_attempts,
                        interval_s=attempt_interval_s,
                    )
                    if not approved:
                        await self._clear_step_context(instance_id)
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason="while_match_no_iterations_not_approved",
                        )
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "while_match_no_iterations_not_approved",
                                    "region": reg,
                                },
                                completed=False,
                            ),
                        )
                    logger.info(
                        "dsl_scenario: while_match no_iterations scenario=%s region=%s "
                        "attempts=%d → soft-fail with retry",
                        _scen(key),
                        reg,
                        initial_attempts,
                    )
                    await self._clear_step_context(instance_id)
                    _trace_row(
                        _resumable_step,
                        step,
                        "early_exit",
                        reason="while_match_no_iterations",
                    )
                    return TaskResult(
                        success=False,
                        next_run_at=datetime.now(tz=UTC) + timedelta(seconds=30),
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "while_match_no_iterations",
                                "region": reg,
                                "attempts": initial_attempts,
                                "interval": attempt_interval_s,
                            },
                            completed=False,
                        ),
                    )

                logger.info(
                    "dsl_scenario: while_match done scenario=%s region=%s iterations=%d",
                    _scen(key),
                    reg,
                    iterations,
                )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok", iterations=iterations)
                continue
            if "while_scroll" in step:
                reg = str(step.get("while_scroll") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                direction = str(step.get("direction") or "up").strip().lower()
                try:
                    delta = int(step.get("delta") or 350)
                except (TypeError, ValueError):
                    delta = 350
                try:
                    duration_ms = int(step.get("duration_ms") or 300)
                except (TypeError, ValueError):
                    duration_ms = 300
                try:
                    max_iters = int(step.get("max") or 30)
                except (TypeError, ValueError):
                    max_iters = 30
                max_iters = max(0, max_iters)
                try:
                    repeats_to_end = int(step.get("repeats_to_end") or 3)
                except (TypeError, ValueError):
                    repeats_to_end = 3
                pause_s = _parse_wait_seconds(step.get("pause_ms") or step.get("pause") or "600ms")

                inner_steps = step.get("steps")
                if not isinstance(inner_steps, list):
                    inner_steps = []

                pair = screen_region_by_name(
                    area_doc,
                    reg,
                    state_flat=self._state_flat(),
                    screen_id=(await _read_current_screen(instance_id, self.redis_client)) or None,
                )
                bbox = pair[1].get("bbox") if pair is not None else None
                if not isinstance(bbox, dict):
                    logger.warning(
                        "dsl_scenario: while_scroll region not found in area.json: %s (scenario=%s)",
                        reg,
                        _scen(key),
                    )
                    _trace_row(_resumable_step, step, "stopped", reason="region_not_found")
                    continue

                try:
                    px = int(round(float(bbox["x"]) / 100.0 * dev_w))
                    py = int(round(float(bbox["y"]) / 100.0 * dev_h))
                    pw = int(round(float(bbox["width"]) / 100.0 * dev_w))
                    ph = int(round(float(bbox["height"]) / 100.0 * dev_h))
                except (KeyError, TypeError, ValueError):
                    _trace_row(_resumable_step, step, "stopped", reason="invalid_bbox")
                    continue
                if pw <= 0 or ph <= 0:
                    _trace_row(_resumable_step, step, "stopped", reason="invalid_bbox")
                    continue

                from analysis.scroll import ScrollEndDetector, fingerprint_region_bgr

                detector = ScrollEndDetector(repeats_to_end=repeats_to_end)
                _capture = getattr(actions, "capture_screen_bgr_cached", actions.capture_screen_bgr)
                _invalidate = getattr(actions, "invalidate_frame_cache", None)

                iterations = 0
                inner_result: TaskResult | None = None
                for i in range(max_iters):
                    if await self._preempted_by_new_debug(instance_id):
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "dsl_preempted_debug",
                                    "preempted": True,
                                },
                                completed=False,
                            ),
                        )
                    # First iter: process the currently-visible page before any swipe.
                    if i > 0 and direction and delta > 0:
                        ok = await asyncio.to_thread(
                            actions.swipe_direction,
                            instance_id,
                            direction=direction,
                            delta=delta,
                            duration_ms=duration_ms,
                        )
                        if not ok:
                            _trace_row(_resumable_step, step, "stopped", reason="swipe_not_approved")
                            return TaskResult(
                                success=False,
                                next_run_at=None,
                                metadata=_fin(
                                    {"scenario": key, "reason": "swipe_not_approved"},
                                    completed=False,
                                ),
                            )
                        if pause_s > 0:
                            await asyncio.sleep(_action_pause_seconds(pause_s))

                    iter_path = f"{_resumable_step}.{i}"
                    for inner_idx, inner in enumerate(inner_steps):
                        if not isinstance(inner, dict):
                            continue
                        result = await self._run_inline_step(
                            inner,
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            trace_path=f"{iter_path}.{inner_idx}",
                        )
                        if result is not None:
                            inner_result = result
                            break
                    if inner_result is not None:
                        break

                    # Fingerprint AFTER inner steps so post-claim state is what we compare.
                    if _invalidate is not None:
                        with suppress(Exception):
                            _invalidate(instance_id)
                    image_bgr = await asyncio.to_thread(_capture, instance_id)
                    patch = image_bgr[py:py + ph, px:px + pw]
                    fp = fingerprint_region_bgr(patch)
                    detector.push(fp)
                    iterations += 1
                    if detector.is_the_end():
                        break

                if inner_result is not None:
                    md = dict(inner_result.metadata or {})
                    _trace_row(
                        _resumable_step,
                        step,
                        "stopped",
                        reason=str(md.get("reason") or ""),
                    )
                    return TaskResult(
                        success=inner_result.success,
                        next_run_at=inner_result.next_run_at,
                        metadata=_fin(md, completed=False),
                    )
                logger.info(
                    "dsl_scenario: while_scroll done scenario=%s region=%s iterations=%d",
                    _scen(key),
                    reg,
                    iterations,
                )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok", iterations=iterations)
                continue
            if "repeat" in step:
                await self._write_step_context(instance_id, scenario=key)
                spec = step.get("repeat")
                if isinstance(spec, dict):
                    try:
                        max_iters = int(spec.get("max", 1))
                    except (TypeError, ValueError):
                        max_iters = 1
                    inner_steps = spec.get("steps")
                    until_match = str(spec.get("until_match") or "").strip()
                    until_any = spec.get("until_any_match")
                else:
                    try:
                        max_iters = int(spec or 1)
                    except (TypeError, ValueError):
                        max_iters = 1
                    inner_steps = step.get("steps")
                    until_match = ""
                    until_any = None

                max_iters = max(0, max_iters)
                if not isinstance(inner_steps, list) or not inner_steps:
                    _trace_row(_resumable_step, step, "skipped_empty")
                    continue

                until_any_list: list[str] = []
                if isinstance(until_any, list):
                    until_any_list = [
                        str(x or "").strip()
                        for x in until_any
                        if str(x or "").strip()
                    ]

                iter_idx_total = 0
                for iter_idx in range(max_iters):
                    if await self._preempted_by_new_debug(instance_id):
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "dsl_preempted_debug",
                                    "preempted": True,
                                },
                                completed=False,
                            ),
                        )
                    if until_match:
                        row = await self._match_region(
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            scenario_key=key,
                            step=step,
                            region=until_match,
                        )
                        if row is not None and bool(row.get("matched")):
                            break
                    if until_any_list:
                        any_hit = False
                        for reg in until_any_list:
                            row2 = await self._match_region(
                                actions=actions,
                                area_doc=area_doc,
                                repo_root=repo_root,
                                instance_id=instance_id,
                                scenario_key=key,
                                step=step,
                                region=reg,
                            )
                            if row2 is not None and bool(row2.get("matched")):
                                any_hit = True
                                break
                        if any_hit:
                            break
                    iter_path = f"{_resumable_step}.{iter_idx}"
                    self._append_trace_row(
                        iter_path, None, "iter", summary=f"iter {iter_idx}"
                    )
                    iter_idx_total = iter_idx + 1
                    try:
                        for inner_idx, inner in enumerate(inner_steps):
                            if not isinstance(inner, dict):
                                continue
                            result = await self._run_inline_step(
                                inner,
                                actions=actions,
                                area_doc=area_doc,
                                repo_root=repo_root,
                                instance_id=instance_id,
                                dev_w=dev_w,
                                dev_h=dev_h,
                                scenario_key=key,
                                trace_path=f"{iter_path}.{inner_idx}",
                            )
                            if result is not None:
                                md = dict(result.metadata or {})
                                _trace_row(
                                    _resumable_step,
                                    step,
                                    "stopped",
                                    reason=str(md.get("reason") or ""),
                                )
                                return TaskResult(
                                    success=result.success,
                                    next_run_at=result.next_run_at,
                                    metadata=_fin(md, completed=False),
                                )
                    except _BreakRepeat:
                        # Stop the nearest loop-like block and continue with the next outer step.
                        break
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok", iterations=iter_idx_total)
                continue
            if "loop" in step:
                # Delegate to the inline implementation: loop guards (`cond` /
                # `ttl`) are re-evaluated each iteration there and inner steps
                # go through the same `_run_inline_step` path that the rest of
                # the DSL uses.
                await self._write_step_context(instance_id, scenario=key)
                result = await self._run_inline_step(
                    step,
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    dev_w=dev_w,
                    dev_h=dev_h,
                    scenario_key=key,
                    trace_path=str(_resumable_step),
                )
                if result is not None:
                    md = dict(result.metadata or {})
                    _trace_row(
                        _resumable_step,
                        step,
                        "stopped",
                        reason=str(md.get("reason") or ""),
                    )
                    return TaskResult(
                        success=result.success,
                        next_run_at=result.next_run_at,
                        metadata=_fin(md, completed=False),
                    )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
            if "push_scenario" in step:
                await self._write_step_context(instance_id, scenario=key)
                spec = step.get("push_scenario")
                if isinstance(spec, dict):
                    name = str(spec.get("name") or "").strip()
                    try:
                        pr = int(spec.get("priority") or self.priority)
                    except (TypeError, ValueError):
                        pr = self.priority
                    delay_s = await _resolve_push_delay_seconds(
                        spec.get("delay"),
                        instance_id=instance_id,
                        redis_async=self.redis_client,
                        player_id=self.player_id,
                    )
                    skip_dup = bool(spec.get("skip_if_duplicate", True))
                else:
                    name = str(spec or "").strip()
                    pr = self.priority
                    delay_s = 0.0
                    skip_dup = True
                if delay_s is None:
                    await _mark_top_level_step_done()
                    _trace_row(
                        _resumable_step, step, "skipped", reason="delay_unresolved"
                    )
                    continue
                if name:
                    await _enqueue_scenario(
                        redis_async=self.redis_client,
                        instance_id=instance_id,
                        player_id=self.player_id,
                        scenario=name,
                        priority=pr,
                        run_at=time.time() + max(0.0, delay_s),
                        skip_if_duplicate=skip_dup,
                    )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
            if "system_back" in step:
                await self._write_step_context(instance_id, scenario=key)
                result = await self._run_system_back_step(
                    actions=actions,
                    instance_id=instance_id,
                    scenario_key=key,
                    step=step,
                    trace_path=str(_resumable_step),
                )
                if result is not None:
                    md = dict(result.metadata or {})
                    return TaskResult(
                        success=result.success,
                        next_run_at=result.next_run_at,
                        metadata=_fin(md, completed=False),
                    )
                continue
            if "swipe_direction" in step:
                await self._write_step_context(instance_id, scenario=key)
                spec = step.get("swipe_direction")
                if isinstance(spec, dict):
                    direction = str(spec.get("direction") or "").strip().lower()
                    try:
                        delta = int(spec.get("delta") or 0)
                    except (TypeError, ValueError):
                        delta = 0
                    try:
                        duration_ms = int(spec.get("duration_ms") or 300)
                    except (TypeError, ValueError):
                        duration_ms = 300
                else:
                    direction = str(spec or "").strip().lower()
                    delta = 350
                    duration_ms = 300
                if direction and delta > 0:
                    ok = await asyncio.to_thread(
                        actions.swipe_direction,
                        instance_id,
                        direction=direction,
                        delta=delta,
                        duration_ms=duration_ms,
                    )
                    if not ok:
                        logger.info(
                            "dsl_scenario: swipe blocked — aborting scenario %s", _scen(key)
                        )
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "stopped", reason="swipe_not_approved")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {"scenario": key, "reason": "swipe_not_approved"},
                                completed=False,
                            ),
                        )
                    await asyncio.sleep(_action_pause_seconds(0.4))
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
            if "ocr" in step:
                reg = str(step.get("ocr") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if reg:
                    ocr_steps = [step]
                    while step_index < len(steps):
                        next_step = steps[step_index]
                        if not isinstance(next_step, dict) or "ocr" not in next_step:
                            break
                        step_index += 1
                        if not await _dsl_cond_allows_step(
                            next_step,
                            instance_id,
                            self.redis_client,
                            state_flat=self._state_flat(),
                        ):
                            logger.debug(
                                "dsl_scenario: step skipped by cond (%s)",
                                next_step.get("cond"),
                            )
                            continue
                        if str(next_step.get("ocr") or "").strip():
                            ocr_steps.append(next_step)
                    if len(ocr_steps) > 1:
                        await self._ocr_region_bulk(
                            actions=actions,
                            area_doc=area_doc,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            steps=ocr_steps,
                        )
                    else:
                        await self._ocr_region(
                            actions=actions,
                            area_doc=area_doc,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            step=step,
                            region=reg,
                        )
                    active_player = await _read_active_player(instance_id, self.redis_client)
                    # Check every region in the bulk batch — not just the first.
                    # ``reg`` here is the OUTER step's region; a bulk batch like
                    # ``[{ocr: a}, {ocr: player.id}, ...]`` would otherwise skip
                    # the identity gate because ``reg == "a"``, even though OCR
                    # *did* try to resolve identity. Region names follow the
                    # ``games/<game>/<id>/area.yaml`` convention (``player.id``
                    # with a dot — see ``games/wos/core/who_i_am/area.yaml``).
                    identity_regions = {
                        str(s.get("ocr") or "").strip() for s in ocr_steps
                    }
                    if (
                        require_identity_resolution
                        and "player.id" in identity_regions
                        and not active_player
                    ):
                        logger.info(
                            "dsl_scenario: identity OCR did not set active_player "
                            "scenario=%s regions=%s — retry",
                            _scen(key),
                            sorted(identity_regions),
                        )
                        await self._clear_step_context(instance_id)
                        _trace_row(
                            _resumable_step,
                            step,
                            "early_exit",
                            reason="identity_not_resolved",
                        )
                        return TaskResult(
                            success=False,
                            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=30),
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "identity_not_resolved",
                                    "region": reg,
                                },
                                completed=False,
                            ),
                        )
                # ``_ocr_audit_step`` set ``self._last_ocr_row`` on the way out
                # — pass it through so the trace shows confidence/value/text.
                # On bulk OCR this reflects the last region; that's acceptable
                # for a single trace row covering a sibling chain.
                await _mark_top_level_step_done()
                _trace_row(
                    _resumable_step, step, "ok", ocr_row=self._last_ocr_row
                )
                continue
            if "exec" in step:
                cmd = str(step.get("exec") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                exec_row: dict[str, Any] = {}
                if cmd:
                    args = {k: v for k, v in step.items() if k not in ("exec", "cond")}
                    exec_row = await self._run_exec_step(cmd, instance_id, args)
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok", **exec_row)
                continue
            if "click" in step:
                reg = str(step.get("click") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                _sf = self._state_flat()
                pair = (
                    screen_region_by_name(area_doc, reg, state_flat=_sf) if reg else None
                )
                # For click approvals: expose region + optional threshold (overlay queue may
                # already set ``current_task_threshold``; do not overwrite).
                if reg and self.redis_client is not None:
                    with suppress(Exception):
                        st_key = f"wos:instance:{instance_id}:state"
                        mapping: dict[str, str] = {"current_task_region": reg}
                        if pair is not None:
                            raw_thr = pair[1].get("threshold")
                            thr_txt = ""
                            if isinstance(raw_thr, (int, float)):
                                thr_txt = f"{float(raw_thr):.6g}"
                            elif isinstance(raw_thr, str) and str(raw_thr).strip():
                                thr_txt = str(raw_thr).strip()
                            if thr_txt:
                                prev = await self.redis_client.hget(
                                    st_key, "current_task_threshold"
                                )
                                prev_s = (
                                    prev.decode()
                                    if isinstance(prev, bytes)
                                    else str(prev or "")
                                ).strip()
                                if not prev_s:
                                    mapping["current_task_threshold"] = thr_txt
                        await self.redis_client.hset(st_key, mapping=mapping)
                if reg:
                    result = await self._tap_region(
                        actions=actions,
                        area_doc=area_doc,
                        repo_root=repo_root,
                        instance_id=instance_id,
                        dev_w=dev_w,
                        dev_h=dev_h,
                        scenario_key=key,
                        region=reg,
                        step=step,
                    )
                    if result is not None:
                        md = dict(result.metadata or {})
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason=str(md.get("reason") or ""),
                            match_row=self._last_match_row,
                        )
                        return TaskResult(
                            success=result.success,
                            next_run_at=result.next_run_at,
                            metadata=_fin(md, completed=False),
                        )
                    await asyncio.sleep(_action_pause_seconds(0.4))
                await _mark_top_level_step_done()
                _trace_row(
                    _resumable_step, step, "ok", match_row=self._last_match_row
                )
                continue
            if "long_click" in step:
                reg = str(step.get("long_click") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if not reg:
                    await _mark_top_level_step_done()
                    _trace_row(_resumable_step, step, "ok")
                    continue
                pair = screen_region_by_name(area_doc, reg, state_flat=self._state_flat())
                if pair is None:
                    _trace_row(_resumable_step, step, "stopped", reason="unknown_region")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "unknown_region"},
                            completed=False,
                        ),
                    )
                _entry, reg_doc = pair
                bbox = reg_doc.get("bbox")
                if not isinstance(bbox, dict):
                    _trace_row(_resumable_step, step, "stopped", reason="missing_bbox")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin({"scenario": key, "reason": "missing_bbox"}, completed=False),
                    )
                raw_dur = step.get("duration")
                if raw_dur is None:
                    raw_dur = step.get("wait")
                duration_ms = 800
                with suppress(Exception):
                    dur_s = _parse_wait_seconds(raw_dur)
                    if dur_s > 0:
                        duration_ms = int(round(dur_s * 1000.0))
                pt = self._point_for_region_action(reg, bbox, dev_w, dev_h)
                ok = False
                with suppress(Exception):
                    ok = bool(
                        await asyncio.to_thread(
                            actions.long_tap,
                            instance_id,
                            pt,
                            duration_ms=duration_ms,
                        )
                    )
                if not ok:
                    _trace_row(_resumable_step, step, "stopped", reason="long_click_not_approved")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "long_click_not_approved"},
                            completed=False,
                        ),
                    )
                await asyncio.sleep(_action_pause_seconds(0.4))
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
            if "ttl" in step:
                # Exit early, reschedule self for ``now + ttl``. Same semantic
                # as the inline handler — mostly used inside ``while_match.else``,
                # but accepted at top level too for the "skip this tick" idiom.
                ttl_s = max(0.0, _parse_wait_seconds(step.get("ttl")))
                await self._clear_step_context(instance_id)
                _trace_row(
                    _resumable_step, step, "early_exit", reason="ttl", ttl_s=ttl_s
                )
                return TaskResult(
                    success=True,
                    next_run_at=datetime.now(tz=UTC) + timedelta(seconds=ttl_s),
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "ttl_exit",
                            "ttl_s": ttl_s,
                        },
                        completed=False,
                    ),
                )
            if "wait_screen" in step:
                await self._write_step_context(instance_id, scenario=key)
                matched = await self._run_wait_screen_step(
                    actions=actions,
                    instance_id=instance_id,
                    scenario_key=key,
                    step=step,
                )
                if not matched:
                    await self._clear_step_context(instance_id)
                    _trace_row(
                        _resumable_step,
                        step,
                        "stopped",
                        reason="wait_screen_timeout",
                    )
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "wait_screen_timeout"},
                            completed=False,
                        ),
                    )
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok", matched=matched)
                continue
            if "wait" in step:
                # Supports "1200ms" (string) or seconds (number).
                w = step.get("wait")
                await self._write_step_context(instance_id, scenario=key)
                from config.loader import get_settings as _get_settings

                _jitter_pct = float(
                    getattr(_get_settings().worker, "wait_jitter_pct", 0.0) or 0.0
                )
                seconds = _jittered_wait_seconds(_parse_wait_seconds(w), _jitter_pct)
                if seconds > 0:
                    await asyncio.sleep(seconds)
                    # Explicit pause ⇒ assume the screen changed during it
                    # (timer ticks, popups animating in). Drop the framebuffer
                    # cache so the next ``match`` / ``ocr`` doesn't reuse the
                    # pre-wait frame.
                    _invalidate = getattr(actions, "invalidate_frame_cache", None)
                    if _invalidate is not None:
                        _invalidate(instance_id)
                await _mark_top_level_step_done()
                _trace_row(_resumable_step, step, "ok")
                continue
        logger.info("dsl_scenario done: %s (%s)", _scen(key), instance_id)
        await self._clear_step_context(instance_id)
        return TaskResult(
            success=True,
            next_run_at=None,
            metadata=_fin({"scenario": key}, completed=True),
        )
