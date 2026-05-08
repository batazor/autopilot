from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from actions.tap import BotActions, _redis, _require_approval
from analysis.overlay import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.types import Point, Region
from tasks.base import TaskResult

logger = logging.getLogger(__name__)

# Simple guard for DSL steps, e.g. ``cond: currentNode != main_city`` (skip when false).
_COND_SCREEN_RE = re.compile(
    r"^\s*(?P<lhs>[\w]+)\s*(?P<op>==|!=)\s*(?P<rhs>[\w.-]+)\s*$",
)
_COND_SCREEN_LHS = frozenset({"currentnode", "current_node", "current_screen"})


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


async def _dsl_cond_allows_step(
    step: dict[str, Any], instance_id: str, redis_async: Any | None
) -> bool:
    raw = step.get("cond")
    if raw is None or isinstance(raw, bool):
        return True
    s = str(raw).strip()
    if not s:
        return True
    cur = await _read_current_screen(instance_id, redis_async)
    return _eval_simple_screen_cond(s, cur)


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
            items = await redis_async.zrangebyscore("wos:queue", "-inf", "+inf")
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
    await redis_async.zadd("wos:queue", {json.dumps(body): float(run_at)})
    return True


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _load_area_json(repo_root: Path) -> dict[str, Any]:
    p = repo_root / "area.json"
    if not p.is_file():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))  # JSON is valid YAML
    except Exception:
        return {}


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
                scenario_key,
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
                scenario_key,
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
        rule: dict[str, Any] = {
            "name": f"dsl.{scenario_key}.{region}.visible",
            "region": region,
            "action": "findIcon",
            "threshold": threshold,
        }
        min_sat = step.get("min_match_saturation")
        if min_sat is not None:
            rule["min_match_saturation"] = min_sat
        image_bgr = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        out = await evaluate_overlay_rules_async(image_bgr, area_doc, repo_root, [rule])
        row = out.get(str(rule["name"]))
        if isinstance(row, dict):
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
        from ocr.client import OcrClient

        planned_store = str(step.get("store") or region).strip()

        async def _ocr_audit(
            status: str,
            *,
            threshold_s: str = "",
            confidence_s: str = "",
            raw_text: str = "",
            value_s: str = "",
        ) -> None:
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

        pair = screen_region_by_name(area_doc, region) if region else None
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning(
                "dsl_scenario: ocr region not found in area.json: %s (scenario=%s)",
                region,
                scenario_key,
            )
            await _ocr_audit("region_not_found")
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
                scenario_key,
            )
            await _ocr_audit("invalid_bbox")
            return
        if pw <= 0 or ph <= 0:
            logger.warning(
                "dsl_scenario: ocr region has zero size: %s (scenario=%s)",
                region,
                scenario_key,
            )
            await _ocr_audit("zero_bbox")
            return

        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for ocr (scenario=%s region=%s)",
                scenario_key,
                region,
            )
            await _ocr_audit("capture_failed")
            return

        try:
            result = await OcrClient().ocr_region(image, Region(px, py, pw, ph))
        except Exception:
            logger.exception(
                "dsl_scenario: OCR call failed (scenario=%s region=%s)",
                scenario_key,
                region,
            )
            await _ocr_audit("ocr_call_failed")
            return

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
                scenario_key,
                region,
                text,
                confidence,
                threshold,
            )
            await _ocr_audit(
                "low_confidence",
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
                    scenario_key,
                    region,
                    text,
                )
                await _ocr_audit(
                    "integer_cast_failed",
                    threshold_s=thr_s,
                    confidence_s=conf_s,
                    raw_text=text,
                )
                return
            value = digits

        store_field = str(step.get("store") or region).strip()
        if not store_field:
            await _ocr_audit(
                "empty_store_field",
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
                scenario_key,
            )
            scope = "player"

        if self.redis_client is None:
            logger.info(
                "dsl_scenario: OCR result not persisted (no redis client). "
                "scenario=%s region=%s field=%s value=%s confidence=%.3f",
                scenario_key,
                region,
                store_field,
                value,
                confidence,
            )
            await _ocr_audit(
                "no_redis_client",
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
                scenario_key,
                region,
                redis_key,
            )
            await _ocr_audit(
                "redis_write_failed",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
                value_s=str(value),
            )
            return

        await _ocr_audit(
            "stored",
            threshold_s=thr_s,
            confidence_s=conf_s,
            raw_text=text,
            value_s=str(value),
        )
        logger.info(
            "dsl_scenario: OCR stored scenario=%s region=%s key=%s field=%s value=%s "
            "confidence=%.3f",
            scenario_key,
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
        else:
            pt = bbox_percent_center_to_device_point(pair[1]["bbox"], dev_w, dev_h)
        tapped = actions.tap(instance_id, pt, approval_region=region)
        if not tapped:
            logger.info(
                "dsl_scenario: tap rejected or blocked — aborting scenario %s",
                scenario_key,
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
                return None

            until_any_list: list[str] = []
            if isinstance(until_any, list):
                until_any_list = [str(x or "").strip() for x in until_any if str(x or "").strip()]

            for _ in range(max_iters):
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
                iterations += 1

            logger.info(
                "dsl_scenario: nested while_match done scenario=%s region=%s iterations=%d",
                scenario_key,
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
                        "dsl_scenario: swipe blocked — aborting scenario %s", scenario_key
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

        actions = BotActions()
        area_doc = _load_area_json(repo_root)
        dev_w, dev_h = actions.screen_resolution(instance_id)

        # Optional root-level `node: <screen>` — navigate the FSM to the target
        # screen before running steps. Lets DSL scenarios skip explicit
        # `click: <btn>` chains when destination is already in screen_graph.
        target_node = str(doc.get("node") or "").strip()
        if target_node:
            nav_ok = await self._navigate_to_node(
                instance_id,
                target_node,
                actions=actions,
                scenario_key=key,
            )
            if not nav_ok:
                await self._clear_step_context(instance_id)
                return TaskResult(
                    success=False,
                    next_run_at=None,
                    metadata={
                        "scenario": key,
                        "reason": "navigation_failed",
                        "target_node": target_node,
                    },
                )

        for step in steps:
            if not isinstance(step, dict):
                continue
            if not await _dsl_cond_allows_step(step, instance_id, self.redis_client):
                logger.debug("dsl_scenario: step skipped by cond (%s)", step.get("cond"))
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
                    return TaskResult(
                        success=True,
                        next_run_at=None,
                        metadata={
                            "scenario": key,
                            "reason": "match_region_not_found",
                            "region": reg,
                        },
                    )
                matched = bool(row.get("matched"))
                if not matched:
                    logger.info(
                        "dsl_scenario: match guard failed — skipping scenario %s region=%s row=%s",
                        key,
                        reg,
                        row,
                    )
                    await self._clear_step_context(instance_id)
                    return TaskResult(
                        success=True,
                        next_run_at=None,
                        metadata={
                            "scenario": key,
                            "reason": "match_guard_failed",
                            "region": reg,
                            "match": row if isinstance(row, dict) else None,
                        },
                    )
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
                iterations = 0
                for _ in range(max_iters):
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
                        break
                    if not bool(row.get("matched")):
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
                            return result
                    iterations += 1
                logger.info(
                    "dsl_scenario: while_match done scenario=%s region=%s iterations=%d",
                    key,
                    reg,
                    iterations,
                )
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
                            return result
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
                            "dsl_scenario: swipe blocked — aborting scenario %s", key
                        )
                        await self._clear_step_context(instance_id)
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata={"scenario": key, "reason": "swipe_not_approved"},
                        )
                    await asyncio.sleep(0.4)
                continue
            if "ocr" in step:
                reg = str(step.get("ocr") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if reg:
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
                continue
            if "exec" in step:
                cmd = str(step.get("exec") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if cmd:
                    await self._run_exec_step(cmd, instance_id)
                continue
            if "set_node" in step:
                node = str(step.get("set_node") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if not node:
                    continue
                ok, req_id = await asyncio.to_thread(
                    _require_approval,
                    instance_id,
                    {
                        "type": "set_node",
                        "set_node": node,
                        "source": {
                            "component": "tasks.dsl_scenario.DslScenarioTask",
                            "note": "DSL set_node step (approval mode)",
                        },
                    },
                )
                if not ok:
                    logger.info(
                        "dsl_scenario: set_node rejected or blocked — aborting scenario %s",
                        key,
                    )
                    await self._clear_step_context(instance_id)
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata={
                            "scenario": key,
                            "reason": "set_node_not_approved",
                        },
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
                        return result
                    await asyncio.sleep(0.4)
                continue
            if "wait" in step:
                # Supports "1200ms" (string) or seconds (number).
                w = step.get("wait")
                await self._write_step_context(instance_id, scenario=key)
                seconds = _parse_wait_seconds(w)
                if seconds > 0:
                    await asyncio.sleep(seconds)
                continue
        logger.info("dsl_scenario done: %s (%s)", key, instance_id)
        await self._clear_step_context(instance_id)
        return TaskResult(success=True, next_run_at=None, metadata={"scenario": key})
