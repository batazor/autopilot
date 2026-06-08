from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from analysis.overlay_compile import get_inline_steps
from analysis.overlay_duration import parse_duration_seconds
from config.paths import repo_root
from config.tracing import (
    overlay_push_scenario_counter,
    overlay_tab_red_dot_idle_counter,
)
from dsl.dsl_schema import (
    DEFAULT_SCENARIO_PRIORITY,
    dsl_scenario_yaml_device_level,
    dsl_scenario_yaml_enabled,
    dsl_scenario_yaml_priority,
)
from layout.area_lookup import region_tap_hold_ms, screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.types import Point
from tasks.dsl_scenario_helpers import _dsl_cond_allows_step, _parse_wait_seconds


def _record_push_scenario(
    *,
    scenario: str,
    screen: str | None,
    region: str | None,
    outcome: str,
) -> None:
    """Emit one ``wos.overlay.push_scenario.count`` sample.

    Tag values are normalised to ``"unknown"`` when missing so Grafana's
    label selectors don't have to special-case the empty string.
    """
    overlay_push_scenario_counter().add(
        1,
        attributes={
            "scenario": scenario or "unknown",
            "screen": (screen or "").strip() or "unknown",
            "region": (region or "").strip() or "unknown",
            "outcome": outcome,
        },
    )

logger = logging.getLogger(__name__)

_REPO_ROOT = repo_root()
_IDLE_TAB_RED_DOT_LOG_TTL_SECONDS = 15.0
_IDLE_TAB_RED_DOT_LAST_LOG: dict[tuple[str, str, str, str, str, str], float] = {}

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


def _overlay_push_priority(payload: dict[str, Any]) -> int | None:
    """Highest effective priority among a matched payload's pushScenario entries."""

    pu = payload.get("pushScenario")
    if not isinstance(pu, list):
        return None
    best: int | None = None
    for item in pu:
        if not isinstance(item, dict):
            continue
        target = str(item.get("name") or item.get("type") or "").strip()
        if not target:
            continue
        pr_raw = item.get("priority")
        if pr_raw is not None:
            try:
                pr = int(pr_raw)
            except (TypeError, ValueError):
                pr = DEFAULT_SCENARIO_PRIORITY
        else:
            scen_pr = dsl_scenario_yaml_priority(_REPO_ROOT, target)
            if scen_pr is not None:
                pr = scen_pr
            else:
                rule_pr = payload.get("priority")
                try:
                    pr = int(rule_pr) if rule_pr is not None else DEFAULT_SCENARIO_PRIORITY
                except (TypeError, ValueError):
                    pr = DEFAULT_SCENARIO_PRIORITY
        best = pr if best is None else max(best, pr)
    return best


def _decode_redis_raw(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode(errors="replace").strip()
    return str(raw).strip()


def _compact_indices(value: Any) -> str:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            try:
                out.append(str(int(item)))
            except (TypeError, ValueError):
                continue
        return ",".join(out)
    return str(value or "").strip()


def _tab_action_from_payload(payload: dict[str, Any]) -> str:
    action = str(payload.get("tab_action") or "").strip()
    if action:
        return action
    pu = payload.get("pushScenario")
    if isinstance(pu, list) and pu:
        return "push_scenario"
    if _compact_indices(payload.get("red_dot_indices")):
        return "red_dots_no_push"
    return "none"


def _record_idle_tab_red_dot(
    *,
    instance_id: str,
    screen: str,
    rule: str,
    region: str,
    active_index: str,
    red_dot_indices: str,
    action: str,
) -> None:
    overlay_tab_red_dot_idle_counter().add(
        1,
        attributes={
            "instance_id": instance_id or "unknown",
            "screen": screen or "unknown",
            "rule": rule or "unknown",
            "region": region or "unknown",
            "active_index": active_index or "unknown",
            "red_dot_indices": red_dot_indices or "none",
            "action": action or "unknown",
        },
    )


if TYPE_CHECKING:
    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerOverlayMixin(_Base):
    _cfg: Any
    _redis: Any
    _queue: Any
    _bot_actions: Any

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

        Two execution paths from a matched rule:
          * ``pushScenario`` items → enqueued as DSL scenarios (device-level).
          * Inline ``steps:`` (``click`` / ``wait`` / cond-guarded variants) →
            executed in-process by :meth:`_execute_inline_overlay_steps`. Use
            rule-level ``ttl:`` to prevent tight loops — the engine skips
            re-evaluating the rule for ``ttl_seconds`` after a match.

        ``pushScenario`` tasks are always **device-level** (``player_id=""``): they do not require
        a configured player; the worker resolves an active player only when the task needs one.
        ``active_player`` (if known at push time) scopes the push-level ``ttl`` self-throttle
        per-player, so an in-flight scenario for player A doesn't block pushes for player B.
        """
        if self._queue is None:
            return
        now = time.time()
        matched_payloads: list[tuple[str, dict[str, Any]]] = []
        push_payloads: list[tuple[int, int, str, dict[str, Any]]] = []
        for order, (rule_name, payload) in enumerate(overlay_results.items()):
            if not isinstance(payload, dict):
                continue
            if not payload.get("matched"):
                continue
            matched_payloads.append((rule_name, payload))
            await self._emit_idle_tab_red_dot_telemetry(rule_name, payload)
            priority = _overlay_push_priority(payload)
            if priority is not None:
                push_payloads.append((priority, order, rule_name, payload))

        # A single overlay frame can contain multiple true signals (for example
        # a toast plus a red-dot badge). Starting all resulting scenarios from
        # the same frame causes navigation churn and state flicker, so pick one
        # push candidate per analyzer tick. Higher priority wins; YAML/order is
        # the deterministic tie-breaker.
        for _priority, _order, _rule_name, payload in sorted(
            push_payloads, key=lambda it: (-it[0], it[1])
        ):
            try:
                handled = await self._enqueue_push_scenarios_from_overlay(
                    payload, player_id="", run_at=now, active_player=active_player
                )
                if handled:
                    break
            except Exception:
                logger.debug("Failed to enqueue pushScenario task(s) from overlay", exc_info=True)

        # Still persist non-push matched overlays (e.g. set_node/text state), but
        # skip lower-priority push payloads once one scenario was handled.
        for _rule_name, payload in matched_payloads:
            if _overlay_push_priority(payload) is not None:
                continue
            try:
                await self._enqueue_push_scenarios_from_overlay(
                    payload, player_id="", run_at=now, active_player=active_player
                )
            except Exception:
                logger.debug("Failed to process non-push overlay payload", exc_info=True)

        # Inline steps run AFTER the push handling so any ``push_scenario``
        # sibling in the rule still hits the queue first. Independent of the
        # "single push per tick" gate above — a rule that detects a red-dot
        # box should both push a follow-up scenario AND tap the box immediately
        # if it lists both.
        for rule_name, payload in matched_payloads:
            try:
                await self._execute_inline_overlay_steps(rule_name, payload)
            except Exception:
                logger.debug(
                    "overlay inline steps: execution failed rule=%s",
                    rule_name,
                    exc_info=True,
                )

    async def _execute_inline_overlay_steps(
        self, rule_name: str, payload: dict[str, Any]
    ) -> None:
        """Execute a rule's inline ``steps:`` block (``click`` / ``wait`` /
        cond-guarded variants) directly on the bot.

        Tight-loop protection is the rule's ``ttl:`` — once a rule matches, the
        engine won't re-evaluate it for ``ttl_seconds``. For inline-click rules
        always set a ``ttl:`` (5s is a reasonable default), otherwise every
        analyzer tick re-fires the click while the red dot lingers.

        Click target resolution: only ``click: <region>`` matching the rule's
        own region is supported here (uses the payload's match coordinates).
        Different regions need a real scenario — the overlay hot path doesn't
        load area.json.
        """
        steps = get_inline_steps(rule_name)
        if not steps:
            return
        actions = self._bot_actions
        if actions is None:
            return
        instance_id = str(self._cfg.instance_id)
        rule_region = str(payload.get("region") or "").strip()
        # Prefer the real match center (``tap_match_*_pct``) — for ``isSearch``
        # / red-dot rules that's where the artifact actually lives. Fall back
        # to bbox center (``tap_*_pct``) for static regions.
        cx_pct = _overlay_metric_float(payload.get("tap_match_x_pct"))
        cy_pct = _overlay_metric_float(payload.get("tap_match_y_pct"))
        if cx_pct is None or cy_pct is None:
            cx_pct = _overlay_metric_float(payload.get("tap_x_pct"))
            cy_pct = _overlay_metric_float(payload.get("tap_y_pct"))

        for step in steps:
            if not isinstance(step, dict):
                continue
            if not await _dsl_cond_allows_step(
                step, instance_id, self._redis, state_flat=None
            ):
                continue

            if "click" in step:
                region = str(step.get("click") or "").strip()
                if not region:
                    continue
                if region != rule_region:
                    logger.warning(
                        "overlay inline click: rule=%s wants to tap %r but only the "
                        "rule's own region (%r) is supported on the overlay hot path",
                        rule_name, region, rule_region,
                    )
                    continue
                if cx_pct is None or cy_pct is None:
                    logger.debug(
                        "overlay inline click: rule=%s missing tap coords — skipping",
                        rule_name,
                    )
                    continue
                try:
                    dev_w, dev_h = actions.screen_resolution(instance_id)
                except Exception:
                    logger.debug(
                        "overlay inline click: screen_resolution failed for %s",
                        instance_id,
                        exc_info=True,
                    )
                    continue
                pt = Point(
                    int(round(cx_pct / 100.0 * dev_w)),
                    int(round(cy_pct / 100.0 * dev_h)),
                )
                hold_ms = 0
                try:
                    pair = screen_region_by_name(load_area_doc(repo_root()), region)
                    if pair is not None:
                        hold_ms = region_tap_hold_ms(pair[1])
                except Exception:
                    logger.debug(
                        "overlay inline click: hold_ms lookup failed rule=%s region=%s",
                        rule_name, region,
                        exc_info=True,
                    )
                tap_kwargs: dict[str, Any] = {"approval_region": region}
                if hold_ms > 0:
                    tap_kwargs["hold_ms"] = hold_ms
                try:
                    tapped = await asyncio.to_thread(
                        actions.tap,
                        instance_id,
                        pt,
                        **tap_kwargs,
                    )
                except Exception:
                    logger.debug(
                        "overlay inline click: tap raised rule=%s region=%s",
                        rule_name, region,
                        exc_info=True,
                    )
                    continue
                if not tapped:
                    logger.info(
                        "overlay inline click: rejected/blocked rule=%s region=%s",
                        rule_name, region,
                    )
                continue

            if "wait" in step:
                try:
                    secs = _parse_wait_seconds(step.get("wait"))
                except Exception:
                    secs = 0.0
                if secs > 0:
                    await asyncio.sleep(secs)
                continue

            logger.debug(
                "overlay inline: unsupported step in rule=%s — keys=%s",
                rule_name, sorted(k for k in step if k != "cond"),
            )

    async def _has_active_scenario(self) -> bool:
        handle = getattr(self, "_current_task_handle", None)
        if handle is not None and not handle.done():
            return True
        if self._redis is None:
            return False
        iid = str(getattr(self._cfg, "instance_id", "") or "").strip()
        if not iid:
            return False
        try:
            raw_running = await self._redis.get(f"wos:queue:running:{iid}")
            if raw_running:
                return True
        except Exception:
            logger.debug("overlay idle telemetry: running key read failed", exc_info=True)
        try:
            vals = await self._redis.hmget(
                f"wos:instance:{iid}:state",
                ["current_task_id", "current_task_type", "current_scenario"],
            )
        except Exception:
            logger.debug("overlay idle telemetry: current task read failed", exc_info=True)
            return False
        return any(_decode_redis_raw(v) for v in vals or [])

    async def _emit_idle_tab_red_dot_telemetry(
        self,
        rule_name: str,
        payload: dict[str, Any],
    ) -> None:
        if str(payload.get("action") or "").strip() != "detectTabs":
            return
        red_dot_indices = _compact_indices(payload.get("red_dot_indices"))
        if not red_dot_indices:
            return
        if await self._has_active_scenario():
            return

        iid = str(getattr(self._cfg, "instance_id", "") or "").strip() or "unknown"
        region = str(payload.get("region") or "").strip() or "unknown"
        screen = str(payload.get("current_screen") or payload.get("set_node") or "").strip()
        if not screen and self._redis is not None:
            with suppress(Exception):
                raw_screen = await self._redis.hget(
                    f"wos:instance:{iid}:state",
                    "current_screen",
                )
                screen = _decode_redis_raw(raw_screen)
        screen = screen or "unknown"
        active_raw = payload.get("active_index")
        active_index = "none" if active_raw is None else str(active_raw)
        action = _tab_action_from_payload(payload)
        targets = [
            str(item.get("type") or item.get("name") or "").strip()
            for item in (payload.get("pushScenario") or [])
            if isinstance(item, dict)
        ]

        _record_idle_tab_red_dot(
            instance_id=iid,
            screen=screen,
            rule=rule_name,
            region=region,
            active_index=active_index,
            red_dot_indices=red_dot_indices,
            action=action,
        )

        key = (iid, screen, rule_name, region, red_dot_indices, action)
        now = time.monotonic()
        last = _IDLE_TAB_RED_DOT_LAST_LOG.get(key, 0.0)
        if now - last < _IDLE_TAB_RED_DOT_LOG_TTL_SECONDS:
            return
        _IDLE_TAB_RED_DOT_LAST_LOG[key] = now
        logger.info(
            "overlay detectTabs idle red dots: instance=%s screen=%s rule=%s "
            "region=%s red_dot_indices=%s active_index=%s action=%s "
            "targets=%s active_page_id=%s red_dot_pages=%s",
            iid,
            screen,
            rule_name,
            region,
            red_dot_indices,
            active_index,
            action,
            targets,
            payload.get("active_page_id"),
            payload.get("red_dot_pages"),
        )

    async def _enqueue_push_scenarios_from_overlay(
        self,
        payload: dict[str, Any],
        *,
        player_id: str,
        run_at: float,
        active_player: str | None = None,
    ) -> bool:
        if self._queue is None:
            return False

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
        current_screen_snap = set_node_snap
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
                if not current_screen_snap:
                    raw_cur = await self._redis.hget(
                        f"wos:instance:{self._cfg.instance_id}:state",
                        "current_screen",
                    )
                    current_screen_snap = (
                        raw_cur.decode()
                        if isinstance(raw_cur, bytes)
                        else str(raw_cur or "")
                    ).strip()
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
                        _record_push_scenario(
                            scenario=t, screen=set_node_snap, region=reg_snap,
                            outcome="no_hero_id",
                        )
                        continue
                    t = t.replace(_HERO_ID_PLACEHOLDER, hid)

                is_device_level = dsl_scenario_yaml_device_level(_REPO_ROOT, t)
                enabled = dsl_scenario_yaml_enabled(_REPO_ROOT, t)
                if enabled is False:
                    logger.debug("overlay: skipping disabled scenario %s", t)
                    _record_push_scenario(
                        scenario=t, screen=set_node_snap, region=reg_snap,
                        outcome="disabled",
                    )
                    continue
                if not is_device_level and not str(active_player or "").strip():
                    logger.debug(
                        "overlay: skipping player-bound scenario %s — active_player missing",
                        t,
                    )
                    _record_push_scenario(
                        scenario=t, screen=set_node_snap, region=reg_snap,
                        outcome="no_active_player",
                    )
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
                            _record_push_scenario(
                                scenario=t, screen=set_node_snap, region=reg_snap,
                                outcome="dup_main_city",
                            )
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
                        _record_push_scenario(
                            scenario=t, screen=set_node_snap, region=reg_snap,
                            outcome="time_throttle",
                        )
                        return True
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
                        _record_push_scenario(
                            scenario=t, screen=set_node_snap, region=reg_snap,
                            outcome="throttled_push_ttl",
                        )
                        # Throttled debounce — no push happened. Return False so
                        # ``_schedule_overlay_matches`` keeps walking the
                        # priority-sorted candidate list and a lower-priority
                        # rule (e.g. ``tabs.strip.advance.has_next_page``) gets
                        # its tick when the top candidate is on cooldown.
                        return False

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
                args_raw = item.get("args")
                scenario_args = dict(args_raw) if isinstance(args_raw, dict) else None

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
                enqueued = await self._queue.schedule(
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
                    args=scenario_args,
                    skip_if_duplicate=True,
                    dedup_ignore_region=True,
                )
                logger.info(
                    "overlay push_scenario enqueue instance=%s current_screen=%r "
                    "scenario=%s player=%s active_player=%s region=%s priority=%s "
                    "run_at=%s score=%s threshold=%s enqueued=%s task_id=%s",
                    self._cfg.instance_id,
                    current_screen_snap,
                    t,
                    player_id,
                    active_player or "",
                    reg_nm,
                    pr,
                    run_at,
                    score,
                    threshold,
                    enqueued,
                    ovl_task_id,
                )
                _record_push_scenario(
                    scenario=t, screen=set_node_snap, region=reg_snap,
                    outcome="enqueued" if enqueued else "duplicate",
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
                return True
        return False
