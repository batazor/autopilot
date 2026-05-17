from __future__ import annotations

import logging
import math
import re
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from analysis.overlay_duration import parse_duration_seconds
from config.paths import repo_root
from scenarios.dsl_schema import (
    DEFAULT_SCENARIO_PRIORITY,
    dsl_scenario_yaml_device_level,
    dsl_scenario_yaml_enabled,
    dsl_scenario_yaml_priority,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = repo_root()

# ``pushScenario.name`` placeholders. Right now only ``${hero_id}`` is wired
# up — extracted from a ``page.heroes.<id>`` current_screen. Add new pattern /
# extractor pairs here when another per-entity overlay rule needs the same
# substitution shape (e.g. ``${building_id}`` from ``page.building.<id>``).
_HERO_ID_PLACEHOLDER = "${hero_id}"
_PAGE_HEROES_SCREEN_RE = re.compile(r"^page\.heroes\.(?P<hero>[a-z0-9_]+)$")
# ``page.heroes.unit`` is the generic detail-page node (used by FSM edges
# when no specific hero has been identified yet); the regex would otherwise
# accept it as a "hero id" called ``unit``. Add reserved subnames here when
# another non-hero ``page.heroes.<x>`` node lands.
_PAGE_HEROES_NON_HERO_SUFFIXES = frozenset({"unit"})


def _overlay_metric_float(value: object) -> float | None:
    """Coerce overlay ``score`` / ``threshold`` for queue + Redis (reject NaN/inf)."""
    if value is None:
        return None
    try:
        x = float(value) if isinstance(value, (int, float, str, bytes, bytearray)) else float(str(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x



if TYPE_CHECKING:
    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerOverlayMixin(_Base):
    _cfg: Any
    _redis: Any
    _queue: Any

    async def _resolve_hero_id_from_screen(self) -> str:
        """Return the hero id encoded in the current Redis ``current_screen``.

        Empty string when Redis is absent, the field is unset, or the screen
        isn't a ``page.heroes.<id>`` page. Used by ``${hero_id}`` substitution
        in ``pushScenario.name``.
        """
        if self._redis is None:
            return ""
        try:
            cur = await self._redis.hget(
                f"wos:instance:{self._cfg.instance_id}:state", "current_screen"
            )
        except Exception:
            logger.debug("overlay: current_screen read failed", exc_info=True)
            return ""
        cur_s = (
            cur.decode() if isinstance(cur, bytes) else str(cur or "")
        ).strip()
        m = _PAGE_HEROES_SCREEN_RE.match(cur_s)
        if not m:
            return ""
        hero = m.group("hero")
        return "" if hero in _PAGE_HEROES_NON_HERO_SUFFIXES else hero

    async def _schedule_overlay_matches(
        self,
        overlay_results: dict[str, Any],
        *,
        active_player: str | None = None,
    ) -> None:
        """Handle matched overlay rules.

        Policy: overlay analysis never enqueues tap actions. It may only enqueue DSL scenarios
        via `pushScenario` (and other non-tap metadata).

        ``pushScenario`` tasks are always **device-level** (``player_id=""``): they do not require
        a configured player; the worker resolves an active player only when the task needs one.
        ``active_player`` (if known at push time) scopes the push-level ``ttl`` self-throttle
        per-player, so an in-flight scenario for player A doesn't block pushes for player B.
        """
        if self._queue is None:
            return
        now = time.time()
        for _name, payload in overlay_results.items():
            if not isinstance(payload, dict):
                continue
            if not payload.get("matched"):
                continue
            try:
                await self._enqueue_push_scenarios_from_overlay(
                    payload, player_id="", run_at=now, active_player=active_player
                )
            except Exception:
                logger.debug("Failed to enqueue pushScenario task(s) from overlay", exc_info=True)

    async def _enqueue_push_scenarios_from_overlay(
        self,
        payload: dict[str, Any],
        *,
        player_id: str,
        run_at: float,
        active_player: str | None = None,
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

        # ``time_seconds`` is set by the overlay engine when a rule with
        # ``action: text`` + ``type: time`` parses its OCR'd value. We treat
        # its presence as "this is a throttle-only rule" (the operator
        # detected an active countdown and wants to suppress further pushes
        # of the named scenario until the timer expires): use the parsed
        # seconds as the ``ttl`` for every ``pushScenario`` entry, write
        # the push_ttl marker with that TTL, and skip the actual push.
        # Other rule types keep the legacy ``pushScenario[].ttl`` semantics.
        ttl_override_raw = payload.get("time_seconds")
        ttl_override: int = 0
        if ttl_override_raw is not None:
            with suppress(TypeError, ValueError):
                ttl_override = int(ttl_override_raw)
        is_time_throttle_rule = ttl_override > 0

        pu = payload.get("pushScenario")
        if isinstance(pu, list):
            for item in pu:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("name") or item.get("type") or "").strip()
                if not t:
                    continue

                # ``${hero_id}`` resolves from ``current_screen`` (only meaningful
                # on per-hero pages). Lets one overlay rule on ``page.heroes.*``
                # fan out to the 62 ``heroes.<hero>.wiki`` scenarios without
                # listing every hero. If the screen isn't a ``page.heroes.<id>``
                # — or current_screen is empty — we drop the push: a literal
                # ``heroes.${hero_id}.wiki`` task_type would just be dead.
                if _HERO_ID_PLACEHOLDER in t:
                    hid = await self._resolve_hero_id_from_screen()
                    if not hid:
                        logger.debug(
                            "overlay: skipping %r — current_screen has no hero_id",
                            t,
                        )
                        continue
                    t = t.replace(_HERO_ID_PLACEHOLDER, hid)

                is_device_level = dsl_scenario_yaml_device_level(_REPO_ROOT, t)
                enabled = dsl_scenario_yaml_enabled(_REPO_ROOT, t)
                if enabled is False:
                    logger.debug("overlay: skipping disabled scenario %s", t)
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

                # Push-level ``ttl`` self-throttle (YAML ``pushScenario[].ttl``).
                # The queue's ``skip_if_duplicate`` only matches items still
                # *pending* — once a task is popped to run, the next overlay
                # tick that sees the same trigger (e.g. ``page.worker.add``
                # while ``assign_worker`` is mid-run) re-enqueues it and the
                # bot ends up running the scenario back-to-back. SET NX EX
                # holds a "recently pushed" marker for ``ttl`` seconds so the
                # repeat push is dropped here, before it reaches the queue.
                # Scoped per-player (active_player at push time) so a busy
                # scenario for player A doesn't starve player B.
                #
                # ``type: time`` rules override the static ``ttl`` with the
                # parsed seconds value AND suppress the actual push — the
                # rule's intent is "block re-runs of this scenario until
                # the in-game timer expires", not "run it again now".
                ttl_s = (
                    ttl_override
                    if is_time_throttle_rule
                    else parse_duration_seconds(item.get("ttl"))
                )
                if ttl_s and ttl_s > 0 and self._redis is not None:
                    ap_for_key = (active_player or "").strip()
                    if ap_for_key:
                        throttle_key = f"wos:player:{ap_for_key}:push_ttl:{t}"
                    else:
                        throttle_key = (
                            f"wos:instance:{self._cfg.instance_id}:push_ttl:{t}"
                        )
                    if is_time_throttle_rule:
                        # SET (not NX) — refresh the throttle to the latest
                        # remaining time on every overlay tick. Otherwise a
                        # 10-minute "first tick" marker would stay stale
                        # even after the in-game timer dropped to 30s.
                        with suppress(Exception):
                            await self._redis.set(
                                throttle_key, "1", ex=int(ttl_s)
                            )
                            logger.info(
                                "overlay: time-throttle set scenario=%s ttl=%ds "
                                "key=%s source_region=%s",
                                t, int(ttl_s), throttle_key, reg_snap,
                            )
                        # Suppress the actual push: the rule's job was just
                        # to write the throttle marker.
                        continue
                    acquired = True
                    try:
                        acquired = bool(
                            await self._redis.set(
                                throttle_key, "1", nx=True, ex=int(ttl_s)
                            )
                        )
                    except Exception:
                        logger.debug(
                            "push_ttl: SET NX EX failed; allowing push",
                            exc_info=True,
                        )
                    if not acquired:
                        logger.debug(
                            "push_ttl: throttled key=%s ttl=%ds",
                            throttle_key,
                            int(ttl_s),
                        )
                        continue

                pr_raw = item.get("priority")
                if pr_raw is not None:
                    try:
                        pr = int(pr_raw)
                    except (TypeError, ValueError):
                        pr = DEFAULT_SCENARIO_PRIORITY
                else:
                    scen_pr = dsl_scenario_yaml_priority(_REPO_ROOT, t)
                    if scen_pr is not None:
                        pr = scen_pr
                    else:
                        rule_pr = payload.get("priority")
                        try:
                            pr = int(rule_pr) if rule_pr is not None else DEFAULT_SCENARIO_PRIORITY
                        except (TypeError, ValueError):
                            pr = DEFAULT_SCENARIO_PRIORITY

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

                ovl_task_id = (
                    f"ovl:{self._cfg.instance_id}:{t}:{uuid.uuid4().hex[:8]}"
                )
                await self._queue.schedule(
                    task_id=ovl_task_id,
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
                if is_device_level:
                    cancel = getattr(self, "_cancel_current_task", None)
                    if cancel is not None:
                        with suppress(Exception):
                            await cancel(
                                f"device-level overlay {t} preempts current task",
                                result_reason="preempted_by_device_level",
                                reschedule=True,
                            )
