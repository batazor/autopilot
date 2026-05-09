from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

import cv2

from actions.tap import BotActions, _redis, _require_approval
from analysis.overlay import evaluate_overlay_rules_async
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.template_match import (
    patch_bgr_from_bbox_percent,
    validate_live_bbox_patch_vs_reference_dims,
)
from layout.types import Point, Region
from tasks.base import TaskResult

logger = logging.getLogger(__name__)

class _BreakRepeat(Exception):
    """Internal control-flow: break the nearest `repeat:` block."""

# ---------------------------------------------------------------------------
# Color checks (dominant color in a bbox)
# ---------------------------------------------------------------------------

_COLOR_WORD_ALIASES: dict[str, str] = {
    "red": "red",
    "blue": "blue",
    "gray": "gray",
    "grey": "gray",
    "green": "green",
    "красный": "red",
    "синий": "blue",
    "серый": "gray",
    "зелёный": "green",
    "зеленый": "green",
}

# Simple guard for DSL steps, e.g. ``cond: currentNode != main_city`` (skip when false).
_COND_SCREEN_RE = re.compile(
    r"^\s*(?P<lhs>[\w]+)\s*(?P<op>==|!=)\s*(?P<rhs>[\w.-]+)\s*$",
)
_COND_SCREEN_LHS = frozenset({"currentnode", "current_node", "current_screen"})

# Instance-state text guards, e.g. ``cond: chapter.task ~= "Upgrade 2"``.
# - lhs is a Redis hash field in `wos:instance:<id>:state`
# - op:
#   - `~=`: case-insensitive substring contains
#   - `==` / `!=`: case-insensitive full-string match
_COND_TEXT_RE = re.compile(
    r'^\s*(?P<lhs>[\w.\-:]+)\s*(?P<op>==|!=|~=)\s*(?P<rhs>"[^"]*"|\'[^\']*\'|.+?)\s*$'
)

# ``repeat`` / ``while_match`` also nest ``steps``; composite blocks use only ``cond`` + ``steps``.
_DSL_STEP_ACTION_KEYS = frozenset({
    "match",
    "while_match",
    "repeat",
    "push_scenario",
    "swipe_direction",
    "ocr",
    "exec",
    "set_node",
    "click",
    "wait",
})


def _dsl_step_summary(step: Any) -> str:
    """Short human-readable label for queue/history step traces."""
    if not isinstance(step, dict):
        return "(invalid)"
    for key in (
        "click",
        "match",
        "while_match",
        "ocr",
        "set_node",
        "swipe_direction",
        "push_scenario",
        "exec",
        "wait",
        "repeat",
    ):
        if key not in step:
            continue
        val = step[key]
        if key in ("click", "match", "while_match", "ocr", "set_node"):
            s = str(val).strip()
            return f"{key}:{s[:48]}{'…' if len(s) > 48 else ''}"
        if key == "repeat":
            return "repeat"
        if key == "swipe_direction":
            return f"swipe:{str(val)[:40]}"
        if key == "push_scenario":
            return f"push:{str(val)[:40]}"
        if key == "exec":
            return f"exec:{str(val)[:40]}"
        if key == "wait":
            return f"wait:{str(val)[:24]}"
    if "steps" in step and isinstance(step.get("steps"), list):
        return f"group({len(step['steps'])})"
    extra = [k for k in step if k != "cond"]
    return ",".join(extra[:5]) or "(empty)"


def _eval_simple_screen_cond(expr: str, current_screen: str) -> bool:
    """Evaluate ``lhs == rhs`` / ``lhs != rhs`` where *lhs* is Redis ``current_screen``."""
    m = _COND_SCREEN_RE.match(expr.strip())
    if not m:
        logger.warning("dsl_scenario: unsupported cond syntax %r — skipping step", expr)
        return False
    lhs_raw = m.group("lhs").strip().lower().replace("-", "_")
    if lhs_raw not in _COND_SCREEN_LHS:
        logger.warning("dsl_scenario: unknown cond lhs %r — skipping step", m.group("lhs"))
        return False
    op = m.group("op")
    rhs = m.group("rhs").strip()
    cur = current_screen.strip()
    if op == "==":
        return cur == rhs
    return cur != rhs


def _decode_redis_value(raw: Any) -> str:
    """Normalise a raw Redis value to a stripped ``str``.

    The async client (``redis.asyncio``) is created without
    ``decode_responses=True`` (see ``worker.instance_worker._connect``), so
    ``hget`` returns ``bytes``. ``str(b"main_city")`` produces the literal
    ``"b'main_city'"`` rather than the value, which silently breaks any
    equality check against the configured node name (e.g. ``cond:
    currentNode != main_city`` would always be true). Always decode bytes
    before returning.
    """

    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw).strip()


async def _read_current_screen(instance_id: str, redis_async: Any | None) -> str:
    key = f"wos:instance:{instance_id}:state"
    field = "current_screen"
    if redis_async is not None:
        try:
            raw = await redis_async.hget(key, field)
            return _decode_redis_value(raw)
        except Exception:
            logger.debug("redis async hget current_screen failed", exc_info=True)
    try:
        return _decode_redis_value(_redis().hget(key, field))
    except Exception:
        logger.debug("redis sync hget current_screen failed", exc_info=True)
        return ""


async def _read_instance_state_field(
    instance_id: str, field: str, redis_async: Any | None
) -> str:
    key = f"wos:instance:{instance_id}:state"
    field = str(field or "").strip()
    if not field:
        return ""
    if redis_async is not None:
        try:
            raw = await redis_async.hget(key, field)
            return _decode_redis_value(raw)
        except Exception:
            logger.debug("redis async hget state field failed", exc_info=True)
    try:
        return _decode_redis_value(_redis().hget(key, field))
    except Exception:
        logger.debug("redis sync hget state field failed", exc_info=True)
        return ""


def _strip_quotes(s: str) -> str:
    s2 = (s or "").strip()
    if len(s2) >= 2 and ((s2[0] == '"' and s2[-1] == '"') or (s2[0] == "'" and s2[-1] == "'")):
        return s2[1:-1]
    # Unicode “smart” quotes (copy-paste / some editors).
    if len(s2) >= 2 and (s2[0] in "\u201c\u2018" and s2[-1] in "\u201d\u2019"):
        return s2[1:-1]
    return s2


async def _eval_instance_text_cond(expr: str, instance_id: str, redis_async: Any | None) -> bool:
    m = _COND_TEXT_RE.match(expr.strip())
    if not m:
        return False
    lhs = str(m.group("lhs") or "").strip()
    op = str(m.group("op") or "").strip()
    rhs = _strip_quotes(str(m.group("rhs") or ""))
    if not lhs:
        return False
    cur = await _read_instance_state_field(instance_id, lhs, redis_async)
    cur_lc = cur.strip().lower()
    rhs_lc = rhs.strip().lower()
    if op == "~=":
        return bool(rhs_lc) and (rhs_lc in cur_lc)
    if op == "==":
        return cur_lc == rhs_lc
    if op == "!=":
        return cur_lc != rhs_lc
    return False


async def _dsl_cond_allows_step(
    step: dict[str, Any], instance_id: str, redis_async: Any | None
) -> bool:
    raw = step.get("cond")
    if raw is None or isinstance(raw, bool):
        return True
    s = str(raw).strip()
    if not s:
        return True
    if _COND_SCREEN_RE.match(s):
        cur = await _read_current_screen(instance_id, redis_async)
        return _eval_simple_screen_cond(s, cur)
    if _COND_TEXT_RE.match(s):
        return await _eval_instance_text_cond(s, instance_id, redis_async)
    logger.warning("dsl_scenario: unsupported cond syntax %r — skipping step", s)
    return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


async def _enqueue_scenario(
    *,
    redis_async: Any | None,
    instance_id: str,
    player_id: str,
    scenario: str,
    priority: int,
    run_at: float,
    skip_if_duplicate: bool,
) -> bool:
    """Enqueue a DSL scenario as a queue item (task_type = scenario key)."""
    if redis_async is None:
        return False
    scenario = str(scenario or "").strip()
    player_id = str(player_id or "").strip()
    instance_id = str(instance_id or "").strip()
    if not scenario or not player_id or not instance_id:
        return False

    # Optional duplicate guard: same (player, task_type) already queued.
    if skip_if_duplicate:
        try:
            items = await redis_async.zrangebyscore(
                f"wos:queue:{instance_id}" if instance_id else "wos:queue:unknown",
                "-inf",
                "+inf",
            )
            for raw in items:
                try:
                    payload = raw.decode() if isinstance(raw, bytes) else str(raw)
                    doc = json.loads(payload)
                    if (
                        str(doc.get("player_id") or "") == player_id
                        and str(doc.get("task_type") or "") == scenario
                    ):
                        return False
                except Exception:
                    continue
        except Exception:
            # If we can't check, still allow enqueue.
            pass

    body: dict[str, object] = {
        "task_id": f"dsl:push:{scenario}:{player_id}:{int(run_at)}",
        "player_id": player_id,
        "task_type": scenario,
        "priority": int(priority),
        "run_at": float(run_at),
        "instance_id": instance_id,
    }
    qkey = f"wos:queue:{instance_id}" if instance_id else "wos:queue:unknown"
    await redis_async.zadd(qkey, {json.dumps(body): float(run_at)})
    return True


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
    except OSError:
        return {}
    return _load_yaml_cached(str(path), st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=512)
def _load_yaml_cached(path_s: str, mtime_ns: int, size: int) -> dict[str, Any]:
    # mtime_ns/size are part of the cache key; they auto-invalidate on file change.
    _ = (mtime_ns, size)
    try:
        raw = yaml.safe_load(Path(path_s).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_area_json(repo_root: Path) -> dict[str, Any]:
    p = repo_root / "area.json"
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))  # JSON is valid YAML
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_wait_seconds(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().lower()
    if s.endswith("ms"):
        return float(s[:-2].strip()) / 1000.0
    if s.endswith("s"):
        return float(s[:-1].strip())
    return 0.0


@dataclass
class DslScenarioTask:
    """Generic runner for imperative DSL scenario YAML.

    This is the bridge that lets us keep scenario logic in YAML, while the worker still executes
    tasks from the Redis queue.
    """

    task_id: str
    player_id: str
    priority: int = 80_000
    cooldown_seconds: int = 1
    is_cooperative: bool = False
    skip_account_check: bool = field(default=True, init=False)
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="dsl_scenario", init=False)

    scenario_key: str = ""
    tap_region: str = ""
    tap_x_pct: float | None = None
    tap_y_pct: float | None = None
    start_step_index: int = 0
    # Last `match:` result (best-effort), used to tap at the actual matched location
    # instead of the static region center when `*_search` is involved.
    _last_match_region: str = field(default="", init=False, repr=False)
    _last_match_row: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _last_tap_region_clicked: str = field(default="", init=False, repr=False)
    _ocr_client: Any | None = field(default=None, init=False, repr=False)
    _exclude_match_top_lefts: dict[str, list[tuple[int, int]]] = field(
        default_factory=dict, init=False, repr=False
    )

    async def _write_step_context(self, instance_id: str, *, scenario: str) -> None:
        if self.redis_client is None:
            return
        with suppress(Exception):
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={"current_scenario": scenario},
            )

    async def _persist_dsl_last_match(
        self,
        instance_id: str,
        *,
        region: str,
        threshold: float,
        row: dict[str, Any] | None,
        detail: str = "",
    ) -> None:
        """Expose last template ``match`` outcome on instance Redis hash for Click approvals UI."""
        if self.redis_client is None:
            return
        thr_s = f"{float(threshold):.6g}"
        score_s = ""
        matched_s = ""
        if isinstance(row, dict):
            sc = row.get("score")
            score_s = "" if sc is None else str(sc)
            matched_s = "1" if bool(row.get("matched")) else "0"
        mapping = {
            "dsl_last_match_region": region,
            "dsl_last_match_threshold": thr_s,
            "dsl_last_match_score": score_s,
            "dsl_last_match_matched": matched_s,
            "dsl_last_match_detail": (detail or "").strip(),
            "dsl_last_match_at": str(time.time()),
        }
        if isinstance(row, dict):
            tl = row.get("top_left")
            tw = row.get("template_w")
            th = row.get("template_h")
            sr = row.get("search_region")
            txp = row.get("tap_x_pct")
            typ = row.get("tap_y_pct")
            tmx = row.get("tap_match_x_pct")
            tmy = row.get("tap_match_y_pct")
            if isinstance(tl, (list, tuple)) and len(tl) >= 2:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_top_left_x"] = str(int(float(tl[0])))
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_top_left_y"] = str(int(float(tl[1])))
            if tw is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_template_w"] = str(int(tw))
            if th is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_template_h"] = str(int(th))
            if sr is not None and str(sr).strip():
                mapping["dsl_last_match_search_region"] = str(sr).strip()
            if txp is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_x_pct"] = f"{float(txp):.6g}"
            if typ is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_y_pct"] = f"{float(typ):.6g}"
            if tmx is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_match_x_pct"] = f"{float(tmx):.6g}"
            if tmy is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_match_y_pct"] = f"{float(tmy):.6g}"
        try:
            await self.redis_client.hset(f"wos:instance:{instance_id}:state", mapping=mapping)
        except Exception:
            logger.debug("dsl_scenario: persist dsl_last_match failed", exc_info=True)

    async def _persist_dsl_last_ocr(self, instance_id: str, mapping: dict[str, str]) -> None:
        """Expose last ``ocr:`` step outcome on instance Redis hash for Click approvals UI."""
        if self.redis_client is None:
            return
        full = dict(mapping)
        full["dsl_last_ocr_at"] = str(time.time())
        try:
            await self.redis_client.hset(f"wos:instance:{instance_id}:state", mapping=full)
        except Exception:
            logger.debug("dsl_scenario: persist dsl_last_ocr failed", exc_info=True)

    async def _persist_dsl_last_color(self, instance_id: str, mapping: dict[str, str]) -> None:
        """Expose last ``color_check:`` step outcome on instance Redis hash for UI/debug."""
        if self.redis_client is None:
            return
        full = dict(mapping)
        full["dsl_last_color_at"] = str(time.time())
        try:
            await self.redis_client.hset(f"wos:instance:{instance_id}:state", mapping=full)
        except Exception:
            logger.debug("dsl_scenario: persist dsl_last_color failed", exc_info=True)

    async def _clear_step_context(self, instance_id: str) -> None:
        if self.redis_client is None:
            return
        with suppress(Exception):
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={"current_scenario": ""},
            )

    async def _navigate_to_node(
        self,
        instance_id: str,
        target_node: str,
        *,
        actions: Any,
        scenario_key: str,
    ) -> bool:
        """Drive the FSM to ``target_node`` via :class:`Navigator` (BFS over screen_graph).

        No-op when ``current_screen`` already equals the target. Unknown / not-in-graph
        targets are treated as soft failures (logged, scenario aborts).
        """
        from navigation.detector import ScreenName
        from navigation.navigator import Navigator

        target_node = target_node.strip()
        if not target_node:
            return True
        try:
            target = ScreenName(target_node)
        except ValueError:
            logger.warning(
                "dsl_scenario: unknown FSM screen %r for scenario %s — skipping navigation",
                target_node,
                _scen(scenario_key),
            )
            return False

        cur = await _read_current_screen(instance_id, self.redis_client)
        if cur == str(target):
            return True

        await self._write_step_context(instance_id, scenario=scenario_key)
        navigator = Navigator(
            actions.capture_screen_bgr,
            actions.tap,
            redis_client=self.redis_client,
        )
        ok = await navigator.navigate_to(target, instance_id)
        if not ok:
            logger.warning(
                "dsl_scenario: navigation to %s failed (scenario=%s instance=%s)",
                target_node,
                _scen(scenario_key),
                instance_id,
            )
            return False
        if self.redis_client is not None:
            try:
                await self.redis_client.hset(
                    f"wos:instance:{instance_id}:state",
                    "current_screen",
                    str(target),
                )
            except Exception:
                logger.debug("dsl_scenario: failed to persist current_screen", exc_info=True)
        return True

    def estimate_duration(self) -> int:
        return 15

    async def _match_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        repo_root: Path,
        instance_id: str,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
    ) -> dict[str, Any] | None:
        pair = screen_region_by_name(area_doc, region) if region else None
        if pair is None:
            logger.warning("dsl_scenario: match region not found in area.json: %s", region)
            await self._persist_dsl_last_match(
                instance_id,
                region=region,
                threshold=0.9,
                row=None,
                detail="region_not_found_in_area",
            )
            return None
        raw_threshold = step.get("threshold")
        if raw_threshold is None:
            raw_threshold = pair[1].get("threshold", 0.9)
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            threshold = 0.9

        # `match:` / `while_match:` should evaluate using the region's action from `area.json`.
        # Historically it always used `findIcon`, which breaks color-only regions (e.g. `isWorkers`).
        area_action = str(pair[1].get("action") or "").strip()
        if area_action not in {"exist", "text", "color_check", "findIcon"}:
            # `click` (and other non-detection actions) cannot be matched; default to `exist`.
            area_action = "exist"

        rule: dict[str, Any] = {
            "name": f"dsl.{scenario_key}.{region}.visible",
            "region": region,
            "action": area_action,
            "threshold": threshold,
        }
        if area_action == "color_check":
            # Color label: prefer step override, else inherit from area.json.
            rule["type"] = str(step.get("type") or pair[1].get("type") or "").strip()
        # When a region has multiple identical icons (mail list), avoid re-hitting the same one.
        excl = self._exclude_match_top_lefts.get(region)
        if excl:
            rule["exclude_top_lefts"] = [[x, y] for (x, y) in excl[-6:]]
            rule["exclude_radius_px"] = 24
        min_sat = step.get("min_match_saturation")
        if min_sat is not None:
            rule["min_match_saturation"] = min_sat
        image_bgr = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        out = await evaluate_overlay_rules_async(image_bgr, area_doc, repo_root, [rule])
        row = out.get(str(rule["name"]))
        if isinstance(row, dict):
            # Keep last match for subsequent `click:` on the same region.
            self._last_match_region = region
            self._last_match_row = row
            await self._persist_dsl_last_match(
                instance_id,
                region=region,
                threshold=threshold,
                row=row,
                detail="",
            )
            return row
        await self._persist_dsl_last_match(
            instance_id,
            region=region,
            threshold=threshold,
            row=None,
            detail="no_overlay_row",
        )
        if self._last_match_region == region:
            self._last_match_region = ""
            self._last_match_row = None
        return None

    async def _ocr_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
    ) -> None:
        """OCR a named region and persist the result to Redis.

        Step shape::

            - ocr: <region_name>
              store: <field>          # default = region name
              scope: player|instance  # default = "player"
                                     # falls back to instance when no player_id
              type: integer|string    # default = inherits area.json `type`
              threshold: 0.7          # confidence floor; default = inherits area.json `threshold`

        The decoded value is written to ``wos:player:<player_id>:state`` (player scope) or
        ``wos:instance:<instance_id>:state`` (instance scope) under ``<store>``, alongside
        ``<store>_text`` (raw OCR text), ``<store>_confidence`` and ``<store>_at`` for
        debugging. Low-confidence reads are logged and skipped, never persisted.
        """
        pair = screen_region_by_name(area_doc, region) if region else None
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning(
                "dsl_scenario: ocr region not found in area.json: %s (scenario=%s)",
                region,
                _scen(scenario_key),
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="region_not_found"
            )
            return

        region_def = pair[1]
        bbox = region_def["bbox"]
        try:
            px = int(round(float(bbox["x"]) / 100.0 * dev_w))
            py = int(round(float(bbox["y"]) / 100.0 * dev_h))
            pw = int(round(float(bbox["width"]) / 100.0 * dev_w))
            ph = int(round(float(bbox["height"]) / 100.0 * dev_h))
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "dsl_scenario: invalid ocr bbox for region %s (scenario=%s)",
                region,
                _scen(scenario_key),
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="invalid_bbox"
            )
            return
        if pw <= 0 or ph <= 0:
            logger.warning(
                "dsl_scenario: ocr region has zero size: %s (scenario=%s)",
                region,
                _scen(scenario_key),
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="zero_bbox"
            )
            return

        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for ocr (scenario=%s region=%s)",
                _scen(scenario_key),
                region,
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="capture_failed"
            )
            return

        try:
            result = await self._get_ocr_client().ocr_region(
                image, Region(px, py, pw, ph)
            )
        except Exception:
            logger.exception(
                "dsl_scenario: OCR call failed (scenario=%s region=%s)",
                _scen(scenario_key),
                region,
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="ocr_call_failed"
            )
            return

        await self._persist_ocr_result(
            instance_id=instance_id,
            scenario_key=scenario_key,
            step=step,
            region=region,
            region_def=region_def,
            result=result,
        )

    async def _ocr_region_bulk(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        steps: list[dict[str, Any]],
    ) -> None:
        requests: list[tuple[dict[str, Any], str, dict[str, Any], Region]] = []

        for step in steps:
            region = str(step.get("ocr") or "").strip()
            if not region:
                continue
            pair = screen_region_by_name(area_doc, region)
            if pair is None or not isinstance(pair[1].get("bbox"), dict):
                logger.warning(
                    "dsl_scenario: ocr region not found in area.json: %s (scenario=%s)",
                    region,
                    _scen(scenario_key),
                )
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="region_not_found"
                )
                continue

            region_def = pair[1]
            bbox = region_def["bbox"]
            try:
                px = int(round(float(bbox["x"]) / 100.0 * dev_w))
                py = int(round(float(bbox["y"]) / 100.0 * dev_h))
                pw = int(round(float(bbox["width"]) / 100.0 * dev_w))
                ph = int(round(float(bbox["height"]) / 100.0 * dev_h))
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "dsl_scenario: invalid ocr bbox for region %s (scenario=%s)",
                    region,
                    _scen(scenario_key),
                )
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="invalid_bbox"
                )
                continue
            if pw <= 0 or ph <= 0:
                logger.warning(
                    "dsl_scenario: ocr region has zero size: %s (scenario=%s)",
                    region,
                    _scen(scenario_key),
                )
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="zero_bbox"
                )
                continue
            requests.append((step, region, region_def, Region(px, py, pw, ph)))

        if not requests:
            return

        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for bulk ocr "
                "(scenario=%s regions=%s)",
                _scen(scenario_key),
                [region for _step, region, _def, _px in requests],
            )
            for step, region, _region_def, _region_px in requests:
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="capture_failed"
                )
            return

        try:
            results = await self._get_ocr_client().ocr_regions(
                image,
                [region_px for _step, _region, _region_def, region_px in requests],
            )
        except Exception:
            logger.exception(
                "dsl_scenario: bulk OCR call failed (scenario=%s regions=%s)",
                _scen(scenario_key),
                [region for _step, region, _def, _px in requests],
            )
            for step, region, _region_def, _region_px in requests:
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="ocr_call_failed"
                )
            return

        logger.info(
            "dsl_scenario: bulk OCR scenario=%s regions=%s",
            _scen(scenario_key),
            [region for _step, region, _def, _px in requests],
        )
        for (step, region, region_def, _region_px), result in zip(
            requests, results, strict=False
        ):
            await self._persist_ocr_result(
                instance_id=instance_id,
                scenario_key=scenario_key,
                step=step,
                region=region,
                region_def=region_def,
                result=result,
            )

    def _get_ocr_client(self) -> Any:
        if self._ocr_client is None:
            from ocr.client import OcrClient

            self._ocr_client = OcrClient()
        return self._ocr_client

    async def _ocr_audit_step(
        self,
        instance_id: str,
        *,
        region: str,
        step: dict[str, Any],
        status: str,
        threshold_s: str = "",
        confidence_s: str = "",
        raw_text: str = "",
        value_s: str = "",
    ) -> None:
        planned_store = str(step.get("store") or region).strip()
        await self._persist_dsl_last_ocr(
            instance_id,
            {
                "dsl_last_ocr_region": region,
                "dsl_last_ocr_store": planned_store,
                "dsl_last_ocr_status": status,
                "dsl_last_ocr_threshold": threshold_s,
                "dsl_last_ocr_confidence": confidence_s,
                "dsl_last_ocr_raw_text": raw_text,
                "dsl_last_ocr_value": value_s,
            },
        )

    async def _persist_ocr_result(
        self,
        *,
        instance_id: str,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
        region_def: dict[str, Any],
        result: Any,
    ) -> None:
        raw_threshold = step.get("threshold")
        if raw_threshold is None:
            raw_threshold = region_def.get("threshold")
        try:
            threshold = float(raw_threshold) if raw_threshold is not None else 0.0
        except (TypeError, ValueError):
            threshold = 0.0

        thr_s = f"{threshold:.6g}"
        text = (result.text or "").strip()
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        conf_s = f"{confidence:.4f}"
        if confidence < threshold:
            logger.warning(
                "dsl_scenario: OCR low confidence — skipping store. scenario=%s region=%s "
                "text=%r confidence=%.3f threshold=%.3f",
                _scen(scenario_key),
                region,
                text,
                confidence,
                threshold,
            )
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="low_confidence",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
            )
            return

        type_hint = str(step.get("type") or region_def.get("type") or "string").strip().lower()
        value: str = text
        if type_hint in {"int", "integer"}:
            digits = re.sub(r"\D+", "", text)
            if not digits:
                logger.warning(
                    "dsl_scenario: OCR integer cast failed — empty digits. "
                    "scenario=%s region=%s text=%r",
                    _scen(scenario_key),
                    region,
                    text,
                )
                await self._ocr_audit_step(
                    instance_id,
                    region=region,
                    step=step,
                    status="integer_cast_failed",
                    threshold_s=thr_s,
                    confidence_s=conf_s,
                    raw_text=text,
                )
                return
            value = digits

        store_field = str(step.get("store") or region).strip()
        if not store_field:
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="empty_store_field",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
            )
            return

        scope = str(step.get("scope") or "player").strip().lower()
        if scope not in {"player", "instance"}:
            logger.warning(
                "dsl_scenario: unknown ocr scope %r — defaulting to 'player' (scenario=%s)",
                scope,
                _scen(scenario_key),
            )
            scope = "player"

        if self.redis_client is None:
            logger.info(
                "dsl_scenario: OCR result not persisted (no redis client). "
                "scenario=%s region=%s field=%s value=%s confidence=%.3f",
                _scen(scenario_key),
                region,
                store_field,
                value,
                confidence,
            )
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="no_redis_client",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
                value_s=str(value),
            )
            return

        if scope == "player" and self.player_id:
            redis_key = f"wos:player:{self.player_id}:state"
        elif scope == "player" and store_field == "player_id" and value:
            # `who_i_am` is intentionally a device-level probe. Once OCR tells
            # us the in-game id, let the rest of the scenario (e.g. fetch_player)
            # continue under that real identity.
            self.player_id = str(value)
            redis_key = f"wos:player:{self.player_id}:state"
        else:
            redis_key = f"wos:instance:{instance_id}:state"

        mapping: dict[str, str] = {
            store_field: str(value),
            f"{store_field}_text": text,
            f"{store_field}_confidence": f"{confidence:.4f}",
            f"{store_field}_at": str(time.time()),
        }
        try:
            await self.redis_client.hset(redis_key, mapping=mapping)
            if store_field == "player_id" and value:
                await self.redis_client.hset(
                    f"wos:instance:{instance_id}:state",
                    mapping={
                        "active_player": str(self.player_id or value),
                        "active_player_at": str(time.time()),
                    },
                )
        except Exception:
            logger.exception(
                "dsl_scenario: failed to persist OCR result (scenario=%s region=%s key=%s)",
                _scen(scenario_key),
                region,
                redis_key,
            )
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="redis_write_failed",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
                value_s=str(value),
            )
            return

        await self._ocr_audit_step(
            instance_id,
            region=region,
            step=step,
            status="stored",
            threshold_s=thr_s,
            confidence_s=conf_s,
            raw_text=text,
            value_s=str(value),
        )
        logger.info(
            "dsl_scenario: OCR stored scenario=%s region=%s key=%s field=%s value=%s "
            "confidence=%.3f",
            _scen(scenario_key),
            region,
            redis_key,
            store_field,
            value,
            confidence,
        )

    async def _run_exec_step(self, name: str, instance_id: str) -> None:
        """Dispatch ``exec: <name>`` to :data:`tasks.dsl_exec.DSL_EXEC_REGISTRY`."""
        from tasks.dsl_exec import DSL_EXEC_REGISTRY, DslExecContext

        fn = DSL_EXEC_REGISTRY.get(name)
        if fn is None:
            logger.warning("dsl_scenario: unknown exec step %r", name)
            return
        ctx = DslExecContext(
            redis_client=self.redis_client,
            player_id=self.player_id,
            instance_id=instance_id,
        )
        try:
            await fn(ctx)
        except Exception:
            logger.exception("dsl_scenario: exec %r failed", name)

    async def _color_check_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
    ) -> bool:
        """Check dominant color inside a named region.

        Note: the DSL no longer has a dedicated `color_check:` step. Color checks are evaluated
        via `match: <region>` when the region in `area.json` uses `action: color_check`.
        """
        raw_want = str(step.get("type") or "").strip().lower()
        want = _COLOR_WORD_ALIASES.get(raw_want, raw_want)
        threshold_raw = step.get("threshold")
        try:
            threshold = float(threshold_raw) if threshold_raw is not None else 0.50
        except (TypeError, ValueError):
            threshold = 0.50
        threshold = max(0.0, min(1.0, threshold))

        pair = screen_region_by_name(area_doc, region) if region else None
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "region_not_found",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        reg_def = pair[1]
        if not want:
            want2 = str(reg_def.get("type") or "").strip().lower()
            want = _COLOR_WORD_ALIASES.get(want2, want2)

        if want not in {"red", "blue", "gray", "green"}:
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "invalid_type",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for color_check (scenario=%s region=%s)",
                _scen(scenario_key),
                region,
            )
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "capture_failed",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        bbox = reg_def["bbox"]
        if not isinstance(bbox, dict):
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "invalid_bbox",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        repo_root = Path(__file__).resolve().parent.parent
        patch, _tl = patch_bgr_from_bbox_percent(image, bbox)
        ph, pw = int(patch.shape[0]), int(patch.shape[1])
        ref_rel = str(pair[0].get("ocr") or "").strip()
        if ref_rel:
            crop_path = exported_crop_png(repo_root, ref_rel, region)
            if crop_path.is_file():
                ref_img = cv2.imread(str(crop_path))
                if ref_img is not None and ref_img.size > 0:
                    ref_ph, ref_pw = int(ref_img.shape[0]), int(ref_img.shape[1])
                    try:
                        validate_live_bbox_patch_vs_reference_dims(
                            pw, ph, ref_pw, ref_ph, reference_label="exported crop"
                        )
                    except ValueError as exc:
                        await self._persist_dsl_last_color(
                            instance_id,
                            {
                                "dsl_last_color_region": region,
                                "dsl_last_color_status": "crop_size_mismatch",
                                "dsl_last_color_want": want,
                                "dsl_last_color_dominant": "",
                                "dsl_last_color_share": "",
                                "dsl_last_color_threshold": f"{threshold:.3f}",
                                "dsl_last_color_detail": str(exc),
                            },
                        )
                        return False

        dominant, shares = dominant_color_label_bgr(patch)
        share = float(shares.get(dominant, 0.0))
        ok = dominant == want and share >= threshold

        await self._persist_dsl_last_color(
            instance_id,
            {
                "dsl_last_color_region": region,
                "dsl_last_color_status": "ok" if ok else "mismatch",
                "dsl_last_color_want": want,
                "dsl_last_color_dominant": dominant,
                "dsl_last_color_share": f"{share:.3f}",
                "dsl_last_color_threshold": f"{threshold:.3f}",
            },
        )
        return ok

    async def _tap_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        region: str,
    ) -> TaskResult | None:
        pair = screen_region_by_name(area_doc, region) if region else None
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning("dsl_scenario: region not found in area.json: %s", region)
            return None

        tap_region = str(self.tap_region or "").strip()
        if (
            self.tap_x_pct is not None
            and self.tap_y_pct is not None
            and (not tap_region or tap_region == region)
        ):
            pt = Point(
                int(round(float(self.tap_x_pct) / 100.0 * dev_w)),
                int(round(float(self.tap_y_pct) / 100.0 * dev_h)),
            )
        elif (
            self._last_match_row is not None
            and self._last_match_region == region
            and bool(self._last_match_row.get("matched"))
            and self._last_match_row.get("tap_x_pct") is not None
            and self._last_match_row.get("tap_y_pct") is not None
        ):
            # Tap at the match center (or match+tap_region delta) computed by overlay engine.
            try:
                txp = float(self._last_match_row.get("tap_x_pct"))  # type: ignore[arg-type]
                typ = float(self._last_match_row.get("tap_y_pct"))  # type: ignore[arg-type]
                pt = Point(
                    int(round(txp / 100.0 * dev_w)),
                    int(round(typ / 100.0 * dev_h)),
                )
            except Exception:
                pt = bbox_percent_center_to_device_point(pair[1]["bbox"], dev_w, dev_h)
        else:
            pt = bbox_percent_center_to_device_point(pair[1]["bbox"], dev_w, dev_h)

        tapped = actions.tap(instance_id, pt, approval_region=region)
        if not tapped:
            logger.info(
                "dsl_scenario: tap rejected or blocked — aborting scenario %s",
                _scen(scenario_key),
            )
            await self._clear_step_context(instance_id)
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={
                    "scenario": scenario_key,
                    "reason": "tap_not_approved",
                },
            )
        self._last_tap_region_clicked = region
        # After a click on a matched region, remember the last match top-left so the next
        # `while_match` can pick a different occurrence if multiple are present.
        if (
            self._last_match_row is not None
            and self._last_match_region == region
            and isinstance(self._last_match_row.get("top_left"), (list, tuple))
            and len(self._last_match_row.get("top_left")) >= 2  # type: ignore[arg-type]
        ):
            try:
                tl = self._last_match_row.get("top_left")
                x0 = int(float(tl[0]))  # type: ignore[index]
                y0 = int(float(tl[1]))  # type: ignore[index]
                self._exclude_match_top_lefts.setdefault(region, []).append((x0, y0))
            except Exception:
                pass
        return None

    async def _run_inline_step(
        self,
        step: dict[str, Any],
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        repo_root: Path,
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
    ) -> TaskResult | None:
        if "break" in step:
            tgt = str(step.get("break") or "").strip().lower()
            if tgt == "repeat":
                raise _BreakRepeat()
            return None
        if "long_click" in step:
            region = str(step.get("long_click") or "").strip()
            if not region:
                return None
            # `wait` (or `duration`) is interpreted as long-press duration.
            duration_ms = 800
            raw_dur = step.get("duration")
            if raw_dur is None:
                raw_dur = step.get("wait")
            try:
                dur_s = _parse_wait_seconds(raw_dur)
                if dur_s > 0:
                    duration_ms = int(round(dur_s * 1000.0))
            except Exception:
                duration_ms = 800

            pair = screen_region_by_name(area_doc, region) if region else None
            if pair is None:
                logger.warning("dsl_scenario: unknown region %r for long_click", region)
                return None
            _entry, reg = pair
            bbox = reg.get("bbox")
            if not isinstance(bbox, dict):
                logger.warning("dsl_scenario: missing bbox for long_click region %r", region)
                return None
            pt = bbox_percent_center_to_device_point(bbox, dev_w, dev_h)
            ok = False
            try:
                ok = bool(actions.long_tap(instance_id, pt, duration_ms=duration_ms))
            except Exception:
                ok = False
            if not ok:
                logger.info(
                    "dsl_scenario: long_click blocked — aborting scenario %s",
                    _scen(scenario_key),
                )
                await self._clear_step_context(instance_id)
                return TaskResult(
                    success=False,
                    next_run_at=None,
                    metadata={"scenario": scenario_key, "reason": "long_click_not_approved"},
                )
            self._last_tap_region_clicked = region
            await asyncio.sleep(0.4)
            return None
        if "click" in step:
            region = str(step.get("click") or "").strip()
            if region:
                result = await self._tap_region(
                    actions=actions,
                    area_doc=area_doc,
                    instance_id=instance_id,
                    dev_w=dev_w,
                    dev_h=dev_h,
                    scenario_key=scenario_key,
                    region=region,
                )
                if result is not None:
                    return result
                await asyncio.sleep(0.4)
            return None
        if "repeat" in step:
            spec = step.get("repeat")
            if isinstance(spec, dict):
                try:
                    max_iters = int(spec.get("max", 1))
                except (TypeError, ValueError):
                    max_iters = 1
                inner_steps = spec.get("steps")
                until_match = str(spec.get("until_match") or "").strip()
                until_any = spec.get("until_any_match")
                stop_click_any = bool(spec.get("stop_after_click"))
                stop_click_regs_raw = spec.get("stop_after_click_regions")
            else:
                try:
                    max_iters = int(spec or 1)
                except (TypeError, ValueError):
                    max_iters = 1
                inner_steps = step.get("steps")
                until_match = ""
                until_any = None
                stop_click_any = False
                stop_click_regs_raw = None

            max_iters = max(0, max_iters)
            if not isinstance(inner_steps, list) or not inner_steps:
                return None

            until_any_list: list[str] = []
            if isinstance(until_any, list):
                until_any_list = [str(x or "").strip() for x in until_any if str(x or "").strip()]

            stop_click_regs: set[str] = set()
            if isinstance(stop_click_regs_raw, list):
                stop_click_regs = {
                    str(x or "").strip()
                    for x in stop_click_regs_raw
                    if str(x or "").strip()
                }

            for _ in range(max_iters):
                self._last_tap_region_clicked = ""
                if until_match:
                    row = await self._match_region(
                        actions=actions,
                        area_doc=area_doc,
                        repo_root=repo_root,
                        instance_id=instance_id,
                        scenario_key=scenario_key,
                        step=step,
                        region=until_match,
                    )
                    if row is not None and bool(row.get("matched")):
                        break
                if until_any_list:
                    for reg in until_any_list:
                        row2 = await self._match_region(
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            scenario_key=scenario_key,
                            step=step,
                            region=reg,
                        )
                        if row2 is not None and bool(row2.get("matched")):
                            return None
                try:
                    for inner in inner_steps:
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
                            scenario_key=scenario_key,
                        )
                        if result is not None:
                            return result
                        if self._last_tap_region_clicked:
                            if stop_click_any or (
                                stop_click_regs
                                and self._last_tap_region_clicked in stop_click_regs
                            ):
                                return None
                except _BreakRepeat:
                    return None
            return None
        if "while_match" in step:
            reg = str(step.get("while_match") or "").strip()
            try:
                max_iters = int(step.get("max", 20))
            except (TypeError, ValueError):
                max_iters = 20
            max_iters = max(0, max_iters)
            inner_steps = step.get("steps")
            if not isinstance(inner_steps, list) or not inner_steps:
                inner_steps = [{"click": reg}]

            iterations = 0
            for _ in range(max_iters):
                row = await self._match_region(
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    scenario_key=scenario_key,
                    step=step,
                    region=reg,
                )
                if row is None or not bool(row.get("matched")):
                    break
                try:
                    for inner in inner_steps:
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
                            scenario_key=scenario_key,
                        )
                        if result is not None:
                            return result
                except _BreakRepeat:
                    # Propagate to the nearest `repeat:` handler.
                    raise
                iterations += 1

            if iterations:
                logger.info(
                    "dsl_scenario: nested while_match done scenario=%s region=%s iterations=%d",
                    _scen(scenario_key),
                    reg,
                    iterations,
                )
            else:
                logger.debug(
                    "dsl_scenario: nested while_match done scenario=%s region=%s iterations=%d",
                    _scen(scenario_key),
                    reg,
                    iterations,
                )
            return None
        if "swipe_direction" in step:
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
                ok = actions.swipe_direction(
                    instance_id, direction=direction, delta=delta, duration_ms=duration_ms
                )
                if not ok:
                    logger.info(
                        "dsl_scenario: swipe blocked — aborting scenario %s",
                        _scen(scenario_key),
                    )
                    await self._clear_step_context(instance_id)
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata={"scenario": scenario_key, "reason": "swipe_not_approved"},
                    )
                await asyncio.sleep(0.4)
            return None
        if "wait" in step:
            seconds = _parse_wait_seconds(step.get("wait"))
            if seconds > 0:
                await asyncio.sleep(seconds)
            return None
        if "push_scenario" in step:
            spec = step.get("push_scenario")
            await self._write_step_context(instance_id, scenario=scenario_key)
            if isinstance(spec, dict):
                name = str(spec.get("name") or "").strip()
                try:
                    pr = int(spec.get("priority") or self.priority)
                except (TypeError, ValueError):
                    pr = self.priority
                try:
                    delay_s = float(spec.get("delay_seconds") or 0.0)
                except (TypeError, ValueError):
                    delay_s = 0.0
                skip_dup = bool(spec.get("skip_if_duplicate", True))
            else:
                name = str(spec or "").strip()
                pr = self.priority
                delay_s = 0.0
                skip_dup = True
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
            return None
        logger.warning("dsl_scenario: unsupported nested while_match step: %s", step)
        return None

    async def execute(self, instance_id: str) -> TaskResult:
        key = str(self.scenario_key or "").strip()
        if not key:
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "missing_scenario_key"},
            )

        repo_root = _repo_root()

        # Resolve scenario by key: search recursively under `scenarios/`, excluding drafts.
        scenarios_root = repo_root / "scenarios"
        if not scenarios_root.is_dir():
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "scenario_root_missing", "path": str(scenarios_root)},
            )

        hits: list[Path] = []
        for p in scenarios_root.rglob(f"{key}.yaml"):
            rel = p.relative_to(scenarios_root).as_posix()
            # Exclude drafts (never execute).
            if rel.startswith("drafts/"):
                continue
            hits.append(p)

        if not hits:
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "scenario_not_found", "key": key},
            )
        # Deterministic: prefer shorter relative path, then lexicographic.
        hits.sort(key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))
        path = hits[0]

        doc = _load_yaml(path)
        steps = doc.get("steps")
        if not isinstance(steps, list):
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "invalid_steps", "path": str(path)},
            )
        steps_total_n = len(steps)
        steps_trace: list[dict[str, Any]] = []

        def _trace_row(i: int, step_obj: Any, status: str, **kw: Any) -> None:
            summ = _dsl_step_summary(step_obj) if isinstance(step_obj, dict) else "(non-dict)"
            row: dict[str, Any] = {"i": i, "summary": summ, "status": status}
            for k, v in kw.items():
                if v is not None:
                    row[k] = v
            steps_trace.append(row)

        def _fin(meta: dict[str, Any], *, completed: bool) -> dict[str, Any]:
            m = dict(meta)
            m["steps_trace"] = list(steps_trace)
            m["steps_total"] = steps_total_n
            m["scenario_completed"] = completed
            if self.start_step_index:
                m["resume_from_step_index"] = int(self.start_step_index)
            return m

        actions = BotActions()
        area_doc = _load_area_json(repo_root)
        dev_w, dev_h = actions.screen_resolution(instance_id)

        # Optional root-level `node: <screen>` — navigate the FSM to the target
        # screen before running steps. Lets DSL scenarios skip explicit
        # `click: <btn>` chains when destination is already in screen_graph.
        target_node = str(doc.get("node") or "").strip()
        # `device_level: true` opts a scenario out of identity gating (see
        # `RedisQueue.pop_due`).  Reused here as the default mode for `while_match`:
        # device-level scenarios (popup dismissals, identity probes) keep the
        # legacy "0 iterations = success" semantics, since their triggers may
        # legitimately have already been resolved.  Player-bound scenarios get
        # initial-probe retries + strict zero-iteration failure so the work
        # actually happens (or is properly retried).
        is_device_level = doc.get("device_level") is True
        if target_node:
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
                await self._clear_step_context(instance_id)
                if self.redis_client is not None:
                    with suppress(Exception):
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            mapping={
                                "nav_error": f"navigation_failed: {key} → {target_node} (no route or verify failed)",
                                "current_screen": "",
                            },
                        )
                    with suppress(Exception):
                        from scheduler.queue import RedisQueue
                        q = RedisQueue(self.redis_client)
                        await q.schedule(
                            task_id=f"nav_fail:where_i_am:{instance_id}:{int(time.time())}",
                            player_id="",
                            task_type="where_i_am",
                            priority=90_000,
                            run_at=time.time(),
                            instance_id=instance_id,
                            skip_if_duplicate=True,
                        )
                return TaskResult(
                    success=False,
                    next_run_at=datetime.now() + timedelta(minutes=5),
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
        while step_index < len(steps):
            step = steps[step_index]
            _resumable_step = step_index  # capture before increment for resume tracking
            step_index += 1
            # Persist current step so hand-pointer resume knows where to continue.
            if self.redis_client is not None:
                with suppress(Exception):
                    await self.redis_client.hset(
                        f"wos:instance:{instance_id}:state",
                        "last_active_scenario_step",
                        str(_resumable_step),
                    )
            if not isinstance(step, dict):
                _trace_row(_resumable_step, step, "skipped_invalid")
                continue
            if not await _dsl_cond_allows_step(step, instance_id, self.redis_client):
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
                for inner in grouped:
                    if not isinstance(inner, dict):
                        continue
                    if not await _dsl_cond_allows_step(inner, instance_id, self.redis_client):
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
                    return TaskResult(
                        success=True,
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
                if not isinstance(inner_steps, list) or not inner_steps:
                    inner_steps = [{"click": reg}]

                # Player-bound scenarios retry the *initial* probe to absorb
                # screen-settling lag after navigation.  Subsequent probes are
                # single-shot — once we've matched once, lack of a match means
                # the work is done.  Device-level scenarios keep legacy 1-shot
                # semantics so popup dismissals don't pause for nothing.
                #
                # YAML form:
                #   retry:
                #     attempts: 5
                #     interval: 500ms     # also accepts "0.5s" or raw seconds
                default_attempts = 1 if is_device_level else 5
                default_interval_s = 0.5
                default_strict = not is_device_level
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
                    probe_attempts = initial_attempts if iterations == 0 else 1
                    matched = False
                    for attempt in range(probe_attempts):
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
                    for inner in inner_steps:
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
                        )
                        if result is not None:
                            inner_result = result
                            break
                    if inner_result is not None:
                        break
                    iterations += 1

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

                if iterations == 0 and strict:
                    # Strict mode: zero iterations after initial-probe retries
                    # means the work didn't happen.  Reschedule so the next
                    # `pop_due` cycle gets another shot instead of yielding to
                    # whatever lower-priority task is in the queue.
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
                        next_run_at=datetime.now() + timedelta(seconds=30),
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
                _trace_row(_resumable_step, step, "ok")
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

                for _ in range(max_iters):
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
                    try:
                        for inner in inner_steps:
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
                        # Stop the nearest repeat and continue with the next outer step.
                        break
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
                    try:
                        delay_s = float(spec.get("delay_seconds") or 0.0)
                    except (TypeError, ValueError):
                        delay_s = 0.0
                    skip_dup = bool(spec.get("skip_if_duplicate", True))
                else:
                    name = str(spec or "").strip()
                    pr = self.priority
                    delay_s = 0.0
                    skip_dup = True
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
                _trace_row(_resumable_step, step, "ok")
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
                    ok = actions.swipe_direction(
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
                    await asyncio.sleep(0.4)
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
                            next_step, instance_id, self.redis_client
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
                _trace_row(_resumable_step, step, "ok")
                continue
            if "exec" in step:
                cmd = str(step.get("exec") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if cmd:
                    await self._run_exec_step(cmd, instance_id)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "set_node" in step:
                node = str(step.get("set_node") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if not node:
                    _trace_row(_resumable_step, step, "skipped_empty")
                    continue
                approval_payload: dict[str, object] = {
                    "type": "set_node",
                    "set_node": node,
                    "source": {
                        "component": "tasks.dsl_scenario.DslScenarioTask",
                        "note": "DSL set_node step (approval mode)",
                    },
                }
                attach_preview = getattr(actions, "attach_approval_preview", None)
                if callable(attach_preview):
                    with suppress(Exception):
                        await asyncio.to_thread(attach_preview, instance_id, approval_payload)
                ok, req_id = await asyncio.to_thread(
                    _require_approval,
                    instance_id,
                    approval_payload,
                )
                if not ok:
                    logger.info(
                        "dsl_scenario: set_node rejected or blocked — aborting scenario %s",
                        _scen(key),
                    )
                    await self._clear_step_context(instance_id)
                    _trace_row(_resumable_step, step, "stopped", reason="set_node_not_approved")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "set_node_not_approved"},
                            completed=False,
                        ),
                    )
                if self.redis_client is not None:
                    with suppress(Exception):
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            "current_screen",
                            node,
                        )
                if req_id is not None:
                    try:
                        _redis().delete(f"wos:ui:click_approval:current:{instance_id}")
                        _redis().delete(f"wos:ui:click_approval:response:{req_id}")
                    except Exception:
                        logger.debug("approval cleanup after set_node failed", exc_info=True)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "click" in step:
                reg = str(step.get("click") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                pair = screen_region_by_name(area_doc, reg) if reg else None
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
                        instance_id=instance_id,
                        dev_w=dev_w,
                        dev_h=dev_h,
                        scenario_key=key,
                        region=reg,
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
                    await asyncio.sleep(0.4)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "long_click" in step:
                reg = str(step.get("long_click") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if not reg:
                    _trace_row(_resumable_step, step, "ok")
                    continue
                pair = screen_region_by_name(area_doc, reg)
                if pair is None:
                    _trace_row(_resumable_step, step, "stopped", reason="unknown_region")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin({"scenario": key, "reason": "unknown_region"}, completed=False),
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
                pt = bbox_percent_center_to_device_point(bbox, dev_w, dev_h)
                ok = False
                with suppress(Exception):
                    ok = bool(actions.long_tap(instance_id, pt, duration_ms=duration_ms))
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
                await asyncio.sleep(0.4)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "wait" in step:
                # Supports "1200ms" (string) or seconds (number).
                w = step.get("wait")
                await self._write_step_context(instance_id, scenario=key)
                seconds = _parse_wait_seconds(w)
                if seconds > 0:
                    await asyncio.sleep(seconds)
                _trace_row(_resumable_step, step, "ok")
                continue
        logger.info("dsl_scenario done: %s (%s)", _scen(key), instance_id)
        await self._clear_step_context(instance_id)
        return TaskResult(
            success=True,
            next_run_at=None,
            metadata=_fin({"scenario": key}, completed=True),
        )
