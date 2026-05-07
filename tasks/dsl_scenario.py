from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from actions.tap import BotActions, _redis, _require_approval
from capture.adb_screencap import DEFAULT_ADB_BIN, adb_screencap_to_file
from config.loader import get_settings
from config.reference_naming import (
    reference_file_basename,
    temporal_png_abs_path,
    unique_label_capture_basename,
)
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from tasks.base import TaskResult

logger = logging.getLogger(__name__)

# Simple guard for DSL steps, e.g. ``cond: currentNode != main_city`` (skip when false).
_COND_SCREEN_RE = re.compile(
    r"^\s*(?P<lhs>[\w]+)\s*(?P<op>==|!=)\s*(?P<rhs>[\w.-]+)\s*$",
)
_COND_SCREEN_LHS = frozenset({"currentnode", "current_node", "current_screen"})


def _eval_simple_screen_cond(expr: str, current_screen: str) -> bool:
    """Evaluate ``lhs == rhs`` / ``lhs != rhs`` where *lhs* is the Redis ``current_screen`` field."""
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


async def _dsl_cond_allows_step(step: dict[str, Any], instance_id: str, redis_async: Any | None) -> bool:
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
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="dsl_scenario", init=False)

    scenario_key: str = ""

    async def _write_step_context(self, instance_id: str, *, scenario: str) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={"current_scenario": scenario},
            )
        except Exception:
            pass

    async def _clear_step_context(self, instance_id: str) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={"current_scenario": ""},
            )
        except Exception:
            pass

    def estimate_duration(self) -> int:
        return 15

    async def execute(self, instance_id: str) -> TaskResult:
        key = str(self.scenario_key or "").strip()
        if not key:
            return TaskResult(success=False, next_run_at=None, metadata={"reason": "missing_scenario_key"})

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

        for step in steps:
            if not isinstance(step, dict):
                continue
            if not await _dsl_cond_allows_step(step, instance_id, self.redis_client):
                logger.debug("dsl_scenario: step skipped by cond (%s)", step.get("cond"))
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
                    try:
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            "current_screen",
                            node,
                        )
                    except Exception:
                        pass
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
                # For click approvals / UI highlighting: always expose the target region.
                if reg and self.redis_client is not None:
                    try:
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            "current_task_region",
                            reg,
                        )
                    except Exception:
                        pass
                if reg:
                    pair = screen_region_by_name(area_doc, reg)
                    if pair is None or not isinstance(pair[1].get("bbox"), dict):
                        logger.warning("dsl_scenario: region not found in area.json: %s", reg)
                    else:
                        pt = bbox_percent_center_to_device_point(pair[1]["bbox"], dev_w, dev_h)
                        tapped = actions.tap(instance_id, pt, approval_region=reg)
                        if not tapped:
                            logger.info(
                                "dsl_scenario: tap rejected or blocked — aborting scenario %s",
                                key,
                            )
                            await self._clear_step_context(instance_id)
                            return TaskResult(
                                success=False,
                                next_run_at=None,
                                metadata={
                                    "scenario": key,
                                    "reason": "tap_not_approved",
                                },
                            )
                        await asyncio.sleep(0.4)
                continue
            if "wait" in step:
                # Supports "1200ms" (string) or seconds (number).
                w = step.get("wait")
                await self._write_step_context(instance_id, scenario=key)
                seconds = 0.0
                if isinstance(w, (int, float)):
                    seconds = float(w)
                else:
                    s = str(w or "").strip().lower()
                    if s.endswith("ms"):
                        seconds = float(s[:-2].strip()) / 1000.0
                    elif s.endswith("s"):
                        seconds = float(s[:-1].strip())
                if seconds > 0:
                    await asyncio.sleep(seconds)
                continue
            if "screenshot" in step:
                raw_ss = step.get("screenshot")
                stem: str
                if raw_ss is None or raw_ss is True or raw_ss == {}:
                    stem = unique_label_capture_basename(instance_id)
                elif isinstance(raw_ss, str) and raw_ss.strip():
                    stem = reference_file_basename(
                        f"{instance_id}_{raw_ss.strip()}",
                        instance_id,
                    )
                elif isinstance(raw_ss, dict):
                    name = str(raw_ss.get("name") or raw_ss.get("basename") or "").strip()
                    stem = (
                        unique_label_capture_basename(instance_id)
                        if not name
                        else reference_file_basename(f"{instance_id}_{name}", instance_id)
                    )
                else:
                    stem = unique_label_capture_basename(instance_id)
                await self._write_step_context(instance_id, scenario=key)
                dest = temporal_png_abs_path(repo_root, stem)
                settings = get_settings()
                adb_bin = (settings.worker.adb_executable or "").strip() or DEFAULT_ADB_BIN
                serial: str | None = None
                for inst in settings.instances:
                    if inst.instance_id == instance_id:
                        serial = inst.bluestacks_window_title
                        break
                if not serial:
                    logger.warning("dsl_scenario screenshot: unknown instance_id %s", instance_id)
                else:
                    ok, msg = await asyncio.to_thread(
                        adb_screencap_to_file,
                        dest,
                        adb_bin=adb_bin,
                        serial=serial,
                    )
                    if ok:
                        logger.info("dsl_scenario screenshot saved: %s", msg)
                    else:
                        logger.warning("dsl_scenario screenshot failed: %s", msg)
                continue

        logger.info("dsl_scenario done: %s (%s)", key, instance_id)
        await self._clear_step_context(instance_id)
        return TaskResult(success=True, next_run_at=None, metadata={"scenario": key})

