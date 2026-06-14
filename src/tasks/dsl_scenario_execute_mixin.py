"""Top-level ``execute`` pipeline for :class:`tasks.dsl_scenario.DslScenarioTask`.

``execute`` owns the scenario pre-flight (doc load, validation, root ``cond``,
identity/navigation gates) and the top-level dispatch loop. The per-step-kind
branch bodies live on the sibling mixins
:class:`tasks.dsl_scenario_step_loops_mixin.DslScenarioStepLoopsMixin` and
:class:`tasks.dsl_scenario_step_actions_mixin.DslScenarioStepActionsMixin`;
context flows to them through one
:class:`tasks.dsl_scenario_exec_frame.ExecFrame` per invocation.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from adb.frame_normalize import GAME_FRAME_SIZE
from config.log_ansi import scenario_log_label as _scen
from dashboard.notifications import push_ui_notification
from dsl import template_resolver as _tmpl
from tasks.base import TaskResult
from tasks.dsl_scenario_exec_frame import ExecFrame
from tasks.dsl_scenario_helpers import (
    _DSL_STEP_ACTION_KEYS,
    _collect_ocr_store_targets,
    _dsl_cond_allows_step,
    _load_area_json,
    _ocr_store_redis_fields,
    _read_current_screen,
)
from tasks.dsl_scenario_step_actions_mixin import DslScenarioStepActionsMixin
from tasks.dsl_scenario_step_loops_mixin import DslScenarioStepLoopsMixin

logger = logging.getLogger(__name__)

# TYPE_CHECKING-only base: gives ty visibility into every host attribute and
# sibling-mixin method without changing the runtime MRO of DslScenarioTask.
# See ``tasks/_dsl_task_host.py`` for the rationale.
if TYPE_CHECKING:
    from tasks._dsl_task_host import _DslTaskHost as _Base
else:
    _Base = object


class DslScenarioExecuteMixin(
    DslScenarioStepLoopsMixin, DslScenarioStepActionsMixin, _Base
):
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
        # screens (e.g. ``tabs.strip.advance`` works from any tabbed sub-page).
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
                navigator = dsl_runtime.navigator(
                    actions,
                    redis_client=self.redis_client,
                )
                try:
                    cur_screen_at_entry = await navigator.detect_current_screen(
                        instance_id,
                        attempts=2,
                        interval_seconds=0.25,
                    )
                except Exception:
                    logger.debug(
                        "dsl_scenario: live screen detect failed before node navigation "
                        "(scenario=%s instance=%s)",
                        _scen(key),
                        instance_id,
                        exc_info=True,
                    )
                    cur_screen_at_entry = ""
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
            # ``tabs.strip.advance`` (``nodes: [shop, shop.daily_deals, ...]``)
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
                # Verify-after-tap race recovery. ``navigate_to`` returns False
                # when its post-tap screen verify loses the race with the rolling
                # detector — but the tap usually *did* land us on the target (the
                # motivating case: ``hall_of_heroes.witness`` pushed off the
                # ``deals.hall_of_heroes.add`` red dot; the ``+`` tap transitions
                # the screen, yet verify confirms a beat late). If the freshly-read
                # ``current_screen`` is one of this scenario's allowed nodes, run
                # the steps in place instead of abandoning the task. Aborting here
                # strands the bot on the event page (red dot still lit, claim loop
                # never runs) and hands control to ``check_main_city`` /
                # ``who_i_am``, which navigate away before the work happens. An
                # operator reject is a *real* stop, so exclude it.
                if (
                    not nav_ok
                    and cur_at_fail
                    and cur_at_fail in allowed_nodes
                    and not rejected_by_operator
                ):
                    nav_ok = True
                    logger.info(
                        "dsl_scenario: %s nav verify missed but current_screen=%s "
                        "is an allowed node — running steps in place",
                        _scen(key), cur_at_fail,
                    )
                    if self.redis_client is not None:
                        with suppress(Exception):
                            await self.redis_client.hset(
                                f"wos:instance:{instance_id}:state",
                                "nav_error", "",
                            )
            if not nav_ok:
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

        async def _mark_top_level_step_done() -> None:
            """After a top-level step finishes, point the UI at the next step index."""
            await self._publish_scenario_step_index(
                instance_id,
                min(fr.step_index, max(steps_total_n - 1, 0)),
            )

        # Per-invocation frame shared with the ``_exec_*_step`` handlers on
        # the sibling step mixins. ``fr.step_index`` mirrors the loop cursor
        # (synced right after each increment); the ``ocr`` handler advances it
        # when it consumes a sibling chain of consecutive ``ocr:`` steps.
        fr = ExecFrame(
            instance_id=instance_id,
            scenario_key=key,
            actions=actions,
            area_doc=area_doc,
            repo_root=repo_root,
            dev_w=dev_w,
            dev_h=dev_h,
            steps=steps,
            require_identity_resolution=require_identity_resolution,
            fin=_fin,
            mark_step_done=_mark_top_level_step_done,
            step_index=step_index,
        )

        while step_index < len(steps):
            step = steps[step_index]
            _resumable_step = step_index  # capture before increment for resume tracking
            step_index += 1
            fr.step_index = step_index
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
                result = await self._exec_grouped_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "match" in step:
                result = await self._exec_match_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "while_match" in step:
                result = await self._exec_while_match_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "while_scroll" in step:
                result = await self._exec_while_scroll_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "repeat" in step:
                result = await self._exec_repeat_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "loop" in step:
                result = await self._exec_loop_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "push_scenario" in step:
                result = await self._exec_push_scenario_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "system_back" in step:
                result = await self._exec_system_back_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "type_text" in step:
                result = await self._exec_type_text_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "swipe_direction" in step:
                result = await self._exec_swipe_direction_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "ocr" in step:
                result = await self._exec_ocr_step(fr, step, _resumable_step)
                # The handler may have consumed a sibling chain of ``ocr:``
                # steps — resync the loop cursor with the frame.
                step_index = fr.step_index
                if result is not None:
                    return result
                continue
            if "exec" in step:
                result = await self._exec_exec_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "click" in step:
                result = await self._exec_click_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "long_click" in step:
                result = await self._exec_long_click_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "ttl" in step:
                result = await self._exec_ttl_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "wait_screen" in step:
                result = await self._exec_wait_screen_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
            if "wait" in step:
                result = await self._exec_wait_step(fr, step, _resumable_step)
                if result is not None:
                    return result
                continue
        logger.info("dsl_scenario done: %s (%s)", _scen(key), instance_id)
        await self._clear_step_context(instance_id)
        return TaskResult(
            success=True,
            next_run_at=None,
            metadata=_fin({"scenario": key}, completed=True),
        )
