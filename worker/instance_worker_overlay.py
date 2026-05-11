from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

# Cap for inline ``runScenario`` execution. Inline scenarios block the overlay
# tick (and therefore the worker's main loop), so a hung scenario must not be
# allowed to halt task processing forever. 5 minutes mirrors the queue-side
# `task_timeout_seconds` (300) — a runScenario *is* the same DSL scenario as
# its queued cousin, so giving it the same budget keeps semantics consistent
# while still bounding any runaway loop.
_INLINE_RUN_SCENARIO_TIMEOUT_SECONDS = 300.0


def _overlay_metric_float(value: object) -> float | None:
    """Coerce overlay ``score`` / ``threshold`` for queue + Redis (reject NaN/inf)."""
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _overlay_effective_priority(payload: dict[str, object]) -> int:
    """Highest priority a matched overlay can enqueue."""
    best = 0
    pr_raw = payload.get("priority")
    with suppress(TypeError, ValueError):
        best = max(best, int(pr_raw)) if pr_raw is not None else best

    pu = payload.get("pushScenario")
    if not isinstance(pu, list):
        pu = payload.get("pushUsecase")
    if isinstance(pu, list):
        for item in pu:
            if not isinstance(item, dict):
                continue
            task = item.get("task")
            src = task if isinstance(task, dict) else item
            pr_raw = src.get("priority")
            try:
                best = max(best, int(pr_raw)) if pr_raw is not None else best
            except (TypeError, ValueError):
                continue

    pr_raw = payload.get("push_task_priority")
    with suppress(TypeError, ValueError):
        best = max(best, int(pr_raw)) if pr_raw is not None else best
    return best


class InstanceWorkerOverlayMixin:
    _cfg: Any
    _redis: Any
    _queue: Any

    async def _schedule_overlay_matches(self, overlay_results: dict[str, object]) -> None:
        """Handle matched overlay rules.

        Policy: overlay analysis never enqueues tap actions. It may only enqueue DSL scenarios
        via `pushScenario` (and other non-tap metadata), or execute them inline via
        `runScenario` (awaited in the overlay tick — for short, cheap scenarios only).

        ``pushScenario`` tasks are always **device-level** (``player_id=""``): they do not require
        a configured player; the worker resolves an active player only when the task needs one.
        """
        now = time.time()
        matched_payloads: list[dict[str, object]] = []
        for _name, payload in overlay_results.items():
            if not isinstance(payload, dict):
                continue
            if not payload.get("matched"):
                continue
            matched_payloads.append(payload)

        matched_payloads.sort(key=_overlay_effective_priority, reverse=True)

        # Inline scenarios first — they're meant to settle state before any queued
        # follow-up pushScenario tasks pop off the queue.
        for payload in matched_payloads:
            rs = payload.get("runScenario")
            if not isinstance(rs, list):
                continue
            for item in rs:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("type") or item.get("name") or "").strip()
                if not key:
                    continue
                try:
                    await self._run_inline_scenario_from_overlay(payload, key)
                except Exception:
                    logger.exception(
                        "overlay runScenario %s failed inline on %s",
                        key,
                        self._cfg.instance_id,
                    )

        if self._queue is None:
            return

        for payload in matched_payloads:
            try:
                await self._enqueue_push_scenarios_from_overlay(
                    payload, player_id="", run_at=now
                )
            except Exception:
                logger.debug("Failed to enqueue pushScenario task(s) from overlay", exc_info=True)

    async def _run_inline_scenario_from_overlay(
        self, payload: dict[str, object], scenario_key: str
    ) -> None:
        """Execute a DSL scenario inline in the overlay tick (no queue)."""
        from tasks.dsl_scenario import DslScenarioTask

        reg_snap = str(payload.get("region") or "").strip() or ""
        tap_x_pct = _overlay_metric_float(payload.get("tap_x_pct"))
        tap_y_pct = _overlay_metric_float(payload.get("tap_y_pct"))

        task = DslScenarioTask(
            task_id=f"ovl-inline:{self._cfg.instance_id}:{scenario_key}:{uuid.uuid4().hex[:8]}",
            player_id="",
            scenario_key=scenario_key,
            tap_region=reg_snap,
            tap_x_pct=tap_x_pct,
            tap_y_pct=tap_y_pct,
            redis_client=self._redis,
        )
        logger.info(
            "[overlay] %s: runScenario inline %s (region=%s)",
            self._cfg.instance_id,
            scenario_key,
            reg_snap or "-",
        )
        try:
            await asyncio.wait_for(
                task.execute(self._cfg.instance_id),
                timeout=_INLINE_RUN_SCENARIO_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.error(
                "[overlay] %s: runScenario inline %s timed out after %.0fs — "
                "yielding overlay tick so the queue keeps moving",
                self._cfg.instance_id,
                scenario_key,
                _INLINE_RUN_SCENARIO_TIMEOUT_SECONDS,
            )
            try:
                from ui.notifications import push_ui_notification

                await push_ui_notification(
                    self._redis,
                    self._cfg.instance_id,
                    kind="overlay.runScenario.timeout",
                    level="warning",
                    message=(
                        f"runScenario `{scenario_key}` timed out after "
                        f"{int(_INLINE_RUN_SCENARIO_TIMEOUT_SECONDS)}s "
                        f"(region={reg_snap or '-'}). Worker resumed; check "
                        "the scenario for an unbounded loop."
                    ),
                    payload={
                        "scenario": scenario_key,
                        "region": reg_snap,
                        "timeout_seconds": _INLINE_RUN_SCENARIO_TIMEOUT_SECONDS,
                    },
                )
            except Exception:
                logger.debug("Failed to push runScenario timeout notification", exc_info=True)

    async def _enqueue_push_scenarios_from_overlay(
        self,
        payload: dict[str, object],
        *,
        player_id: str,
        run_at: float,
    ) -> None:
        if self._queue is None:
            return

        reg_snap = str(payload.get("region") or "").strip() or None
        tap_x_pct = _overlay_metric_float(payload.get("tap_x_pct"))
        tap_y_pct = _overlay_metric_float(payload.get("tap_y_pct"))
        tap_match_x_pct = _overlay_metric_float(payload.get("tap_match_x_pct"))
        tap_match_y_pct = _overlay_metric_float(payload.get("tap_match_y_pct"))
        top_left = payload.get("top_left")
        template_w = payload.get("template_w")
        template_h = payload.get("template_h")
        threshold_snap = _overlay_metric_float(payload.get("threshold"))
        score_snap = _overlay_metric_float(payload.get("score"))
        tpl_bright_snap = _overlay_metric_float(payload.get("template_bright_ratio"))
        patch_bright_snap = _overlay_metric_float(payload.get("patch_bright_ratio"))
        set_node_snap = str(payload.get("set_node") or "").strip()
        if self._redis is not None:
            try:
                snap: dict[str, str] = {}
                if set_node_snap:
                    snap["current_screen"] = set_node_snap
                action = str(payload.get("action") or "").strip()
                if action == "text":
                    txt = str(payload.get("text") or "").strip()
                    conf = payload.get("confidence")
                    snap["last_overlay_text"] = txt
                    if conf is not None and str(conf).strip() != "":
                        with suppress(TypeError, ValueError):
                            snap["last_overlay_confidence"] = f"{float(conf):.4f}"
                    if reg_snap:
                        snap[reg_snap] = txt
                        snap[f"{reg_snap}_text"] = txt
                        if conf is not None and str(conf).strip() != "":
                            with suppress(TypeError, ValueError):
                                snap[f"{reg_snap}_confidence"] = f"{float(conf):.4f}"
                        snap[f"{reg_snap}_at"] = str(time.time())
                if reg_snap:
                    snap["last_overlay_match_region"] = reg_snap
                if threshold_snap is not None:
                    snap["last_overlay_match_threshold"] = f"{threshold_snap:.6g}"
                if score_snap is not None:
                    snap["last_overlay_match_score"] = f"{score_snap:.6g}"
                if tpl_bright_snap is not None:
                    snap["last_overlay_template_bright_ratio"] = f"{tpl_bright_snap:.6g}"
                if patch_bright_snap is not None:
                    snap["last_overlay_patch_bright_ratio"] = f"{patch_bright_snap:.6g}"
                if tap_match_x_pct is not None:
                    snap["last_overlay_match_x_pct"] = f"{tap_match_x_pct:.6g}"
                if tap_match_y_pct is not None:
                    snap["last_overlay_match_y_pct"] = f"{tap_match_y_pct:.6g}"
                if isinstance(top_left, (list, tuple)) and len(top_left) >= 2:
                    snap["last_overlay_match_top_left_x"] = str(int(float(top_left[0])))
                    snap["last_overlay_match_top_left_y"] = str(int(float(top_left[1])))
                if template_w is not None:
                    with suppress(TypeError, ValueError):
                        snap["last_overlay_template_w"] = str(int(template_w))
                if template_h is not None:
                    with suppress(TypeError, ValueError):
                        snap["last_overlay_template_h"] = str(int(template_h))
                if snap:
                    await self._redis.hset(
                        f"wos:instance:{self._cfg.instance_id}:state",
                        mapping=snap,
                    )
            except Exception:
                logger.debug("overlay enqueue: Redis snapshot failed", exc_info=True)

        pu = payload.get("pushScenario")
        if not isinstance(pu, list):
            # Backward compat
            pu = payload.get("pushUsecase")

        if isinstance(pu, list):
            for item in pu:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("name") or item.get("type") or "").strip()
                if not t:
                    continue

                # Special-case: avoid churning `set_node_main_city` when already on main_city.
                # Scenario has `cond`, but repeated enqueue/execute can starve the queue.
                if (
                    t == "set_node_main_city"
                    and self._redis is not None
                ):
                    with suppress(Exception):
                        cur = await self._redis.hget(
                            f"wos:instance:{self._cfg.instance_id}:state",
                            "current_screen",
                        )
                        cur_s = (
                            cur.decode() if isinstance(cur, bytes) else str(cur or "")
                        ).strip()
                        if cur_s == "main_city":
                            continue

                pr_raw = item.get("priority")
                pr = int(pr_raw) if pr_raw is not None else 80_000

                reg_nm = reg_snap
                threshold = threshold_snap
                score = score_snap

                # Pass match box data through the queue for UI/debug (best-effort).
                mtlx_i = None
                mtly_i = None
                tw_i = None
                th_i = None
                if isinstance(top_left, (list, tuple)) and len(top_left) >= 2:
                    with suppress(TypeError, ValueError):
                        mtlx_i = int(float(top_left[0]))
                    with suppress(TypeError, ValueError):
                        mtly_i = int(float(top_left[1]))
                with suppress(TypeError, ValueError):
                    tw_i = int(template_w) if template_w is not None else None
                with suppress(TypeError, ValueError):
                    th_i = int(template_h) if template_h is not None else None

                await self._queue.schedule(
                    task_id=f"ovl:{self._cfg.instance_id}:{t}:{uuid.uuid4().hex[:8]}",
                    player_id=player_id,
                    task_type=t,
                    priority=pr,
                    run_at=run_at,
                    instance_id=self._cfg.instance_id,
                    region=reg_nm,
                    tap_x_pct=tap_x_pct,
                    tap_y_pct=tap_y_pct,
                    match_top_left_x=mtlx_i,
                    match_top_left_y=mtly_i,
                    template_w=tw_i,
                    template_h=th_i,
                    tap_match_x_pct=tap_match_x_pct,
                    tap_match_y_pct=tap_match_y_pct,
                    threshold=threshold,
                    score=score,
                    skip_if_duplicate=True,
                    dedup_ignore_region=True,
                )
            return

        push_t = str(payload.get("push_task_type") or "").strip()
        if not push_t:
            return
        pr_raw = payload.get("push_task_priority")
        pr = int(pr_raw) if pr_raw is not None else 80_000
        reg_nm = reg_snap
        threshold = threshold_snap
        score = score_snap

        await self._queue.schedule(
            task_id=f"ovl:{self._cfg.instance_id}:{push_t}:{uuid.uuid4().hex[:8]}",
            player_id=player_id,
            task_type=push_t,
            priority=pr,
            run_at=run_at,
            instance_id=self._cfg.instance_id,
            region=reg_nm,
            tap_x_pct=tap_x_pct,
            tap_y_pct=tap_y_pct,
            threshold=threshold,
            score=score,
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )
