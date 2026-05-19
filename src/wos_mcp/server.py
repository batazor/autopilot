from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
from mcp.server.fastmcp import FastMCP
from PIL import Image

from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.reference_basename import (
    normalize_reference_basename,
    rename_reference_basename,
    suggest_reference_basename,
)
from navigation.detector import ScreenName
from omniparser.client import check_omniparser_health, parse_screenshot
from omniparser.supervision_bridge import (
    build_omniparser_proposal_regions,
    parsed_element_to_dict,
)
from scenarios import template_resolver
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY, dsl_scenario_yaml_device_level, dsl_scenario_yaml_priority
from services import get_bot_actions, get_repo_root, get_scheduler_async_redis, get_scheduler_queue
from tasks import dsl_runtime
from tasks.dsl_scenario import DslScenarioTask
from tasks.dsl_scenario_helpers import _read_active_player, _read_current_screen

mcp = FastMCP(
    "whiteout-survival-autopilot",
    instructions=(
        "Live rehearsal tools for Whiteout Survival scenarios. "
        "All clicks and screenshots go through the bot runtime APIs, not raw adb shell commands."
    ),
)


def _repo() -> Path:
    return get_repo_root()


def _area_doc() -> dict[str, Any]:
    return load_area_doc(_repo())


def _safe_output_path(output: str | None, *, default_name: str) -> Path:
    rel = str(output or "").strip() or f"references/temporal/{default_name}"
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        msg = "output must be a repo-relative path without '..'"
        raise ValueError(msg)
    return _repo() / path


def _bgr_frame_to_pil(frame: Any) -> Image.Image:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _omni_stats_dict(stats: Any) -> dict[str, int]:
    return {
        "raw_element_count": int(stats.raw_element_count),
        "skipped_min_area": int(stats.skipped_min_area),
        "after_min_area_count": int(stats.after_min_area_count),
        "after_nms_count": int(stats.after_nms_count),
        "nms_removed": int(stats.nms_removed),
        "blacklist_skipped": int(stats.blacklist_skipped),
    }


def _step_region(step: dict[str, Any]) -> str:
    for key in ("while_match", "match", "click", "long_click", "ocr"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _navigator() -> Any:
    actions = get_bot_actions()
    redis = await get_scheduler_async_redis()
    return dsl_runtime.navigator(actions, redis_client=redis)


@mcp.tool()
async def ensure_game_foreground(instance_id: str = "bs1") -> dict[str, Any]:
    """Bring Whiteout Survival to foreground through BotActions/AdbController."""

    actions = get_bot_actions()
    before = actions.is_game_foreground(instance_id)
    actions.ensure_game_foreground(instance_id)
    after = actions.is_game_foreground(instance_id)
    return {"instance_id": instance_id, "was_foreground": bool(before), "is_foreground": bool(after)}


@mcp.tool()
async def capture_screen(
    instance_id: str = "bs1",
    output: str | None = None,
) -> dict[str, Any]:
    """Capture the current screen through BotActions and save it under the repo."""

    actions = get_bot_actions()
    frame = actions.capture_screen_bgr(instance_id)
    default = f"mcp_{instance_id}_{int(time.time())}.png"
    path = _safe_output_path(output, default_name=default)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), frame)
    if not ok:
        msg = f"failed to write screenshot: {path}"
        raise RuntimeError(msg)
    return {
        "instance_id": instance_id,
        "path": path.relative_to(_repo()).as_posix(),
        "width": int(frame.shape[1]),
        "height": int(frame.shape[0]),
    }


@mcp.tool()
async def omni_health(url: str | None = None) -> dict[str, Any]:
    """Check whether the configured OmniParser backend is ready."""

    return dict(check_omniparser_health(url=url))


@mcp.tool()
async def omni_parse_screen(
    instance_id: str = "bs1",
    output: str | None = None,
    screenshot_output: str | None = None,
    url: str | None = None,
    timeout_seconds: int | None = None,
    box_threshold: float = 0.05,
    iou_threshold: float = 0.1,
    min_area_pct: float = 0.04,
    use_paddleocr: bool = True,
    imgsz: int | None = None,
    detect_first: bool = False,
) -> dict[str, Any]:
    """Capture the live screen and run OmniParser for preliminary UI recognition."""

    current = None
    if detect_first:
        detected = await detect_screen(instance_id=instance_id, attempts=1, interval_seconds=0.0)
        current = detected.get("current_screen")

    actions = get_bot_actions()
    frame = actions.capture_screen_bgr(instance_id)
    pil = _bgr_frame_to_pil(frame)

    screenshot_rel = None
    if screenshot_output:
        shot_path = _safe_output_path(screenshot_output, default_name=f"mcp_omni_{instance_id}_{int(time.time())}.png")
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        pil.save(shot_path, format="PNG")
        screenshot_rel = shot_path.relative_to(_repo()).as_posix()

    parsed = parse_screenshot(
        pil,
        url=url,
        timeout_seconds=timeout_seconds,
        box_threshold=float(box_threshold),
        iou_threshold=float(iou_threshold),
        use_paddleocr=bool(use_paddleocr),
        imgsz=imgsz,
    )
    proposal_regions, stats = build_omniparser_proposal_regions(
        parsed.elements,
        pil,
        width=parsed.width,
        height=parsed.height,
        min_area_pct=float(min_area_pct),
        nms_iou_threshold=float(iou_threshold),
    )
    result = {
        "instance_id": instance_id,
        "current_screen": current,
        "width": int(parsed.width),
        "height": int(parsed.height),
        "params": {
            "box_threshold": float(box_threshold),
            "iou_threshold": float(iou_threshold),
            "min_area_pct": float(min_area_pct),
            "use_paddleocr": bool(use_paddleocr),
            "imgsz": int(imgsz) if imgsz is not None else None,
        },
        "elements": [parsed_element_to_dict(el) for el in parsed.elements],
        "proposal_regions": proposal_regions,
        "stats": _omni_stats_dict(stats),
    }
    if screenshot_rel:
        result["screenshot_path"] = screenshot_rel

    out_path = _safe_output_path(output, default_name=f"mcp_omni_{instance_id}_{int(time.time())}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["path"] = out_path.relative_to(_repo()).as_posix()
    return result


@mcp.tool()
async def detect_screen(
    instance_id: str = "bs1",
    attempts: int = 1,
    interval_seconds: float = 0.0,
) -> dict[str, Any]:
    """Detect the current node through Navigator/ScreenDetector and persist it to Redis."""

    nav = await _navigator()
    screen = await nav.detect_current_screen(
        instance_id,
        attempts=max(1, int(attempts)),
        interval_seconds=max(0.0, float(interval_seconds)),
    )
    return {"instance_id": instance_id, "current_screen": screen}


@mcp.tool()
async def navigate_to(
    target_node: str,
    instance_id: str = "bs1",
) -> dict[str, Any]:
    """Navigate to a node using the same Navigator used by DSL scenarios."""

    nav = await _navigator()
    try:
        target = ScreenName(str(target_node).strip())
    except ValueError as exc:
        msg = f"unknown node {target_node!r}; add it to screen_verify.yaml first"
        raise ValueError(msg) from exc
    ok = await nav.navigate_to(target, instance_id)
    redis = await get_scheduler_async_redis()
    current = await _read_current_screen(instance_id, redis)
    return {
        "instance_id": instance_id,
        "target_node": str(target),
        "ok": bool(ok),
        "current_screen": current,
    }


@mcp.tool()
async def check_scenario_step(
    scenario_key: str,
    step_index: int,
    instance_id: str = "bs1",
    detect_first: bool = True,
) -> dict[str, Any]:
    """Evaluate a top-level DSL step's match guard on the live screen without clicking."""

    if detect_first:
        await detect_screen(instance_id=instance_id, attempts=1, interval_seconds=0.0)

    loaded = template_resolver.load_doc(_repo(), scenario_key)
    if loaded is None:
        msg = f"scenario not found: {scenario_key}"
        raise ValueError(msg)
    path, doc = loaded
    steps = doc.get("steps")
    if not isinstance(steps, list):
        msg = f"scenario has no steps list: {scenario_key}"
        raise TypeError(msg)
    idx = int(step_index)
    if idx < 0 or idx >= len(steps):
        msg = f"step_index {idx} out of range for {scenario_key} ({len(steps)} steps)"
        raise IndexError(msg)
    step = steps[idx]
    if not isinstance(step, dict):
        msg = f"step {idx} is not a mapping"
        raise TypeError(msg)

    redis = await get_scheduler_async_redis()
    actions = get_bot_actions()
    current = await _read_current_screen(instance_id, redis)
    region = _step_region(step)
    task = DslScenarioTask(
        task_id=f"mcp-check:{scenario_key}:{idx}",
        player_id=await _read_active_player(instance_id, redis) or "mcp",
        scenario_key=scenario_key,
        redis_client=redis,
    )
    row = None
    if region and ("match" in step or "while_match" in step):
        row = await task._match_region(
            actions=actions,
            area_doc=_area_doc(),
            repo_root=_repo(),
            instance_id=instance_id,
            scenario_key=scenario_key,
            step=step,
            region=region,
        )
    return {
        "instance_id": instance_id,
        "scenario": scenario_key,
        "scenario_path": path.relative_to(_repo()).as_posix(),
        "step_index": idx,
        "current_screen": current,
        "region": region,
        "matched": bool(row and row.get("matched")),
        "row": row,
    }


@mcp.tool()
async def tap_region(
    region: str,
    instance_id: str = "bs1",
    screen_id: str | None = None,
    detect_first: bool = True,
) -> dict[str, Any]:
    """Tap a region through BotActions.tap, scoped to the current detected node."""

    if detect_first:
        await detect_screen(instance_id=instance_id, attempts=1, interval_seconds=0.0)
    redis = await get_scheduler_async_redis()
    current = str(screen_id or "").strip() or await _read_current_screen(instance_id, redis)
    if not current:
        msg = "current_screen is unknown; run detect_screen or pass screen_id"
        raise RuntimeError(msg)

    pair = screen_region_by_name(_area_doc(), region)
    if pair is None:
        msg = f"region {region!r} not found on screen {current!r}"
        raise ValueError(msg)
    _entry, reg = pair
    bbox = reg.get("bbox")
    if not isinstance(bbox, dict):
        msg = f"region {region!r} has no bbox"
        raise TypeError(msg)

    actions = get_bot_actions()
    dev_w, dev_h = actions.screen_resolution(instance_id)
    point = bbox_percent_center_to_device_point(bbox, dev_w, dev_h)
    ok = actions.tap(
        instance_id,
        point,
        approval_region=region,
        approval_source="mcp.rehearsal",
        approval_context={"current_screen": current},
    )
    return {
        "instance_id": instance_id,
        "current_screen": current,
        "region": region,
        "x": int(point.x),
        "y": int(point.y),
        "ok": bool(ok),
    }


@mcp.tool()
async def push_scenario(
    scenario_key: str,
    instance_id: str = "bs1",
    player_id: str | None = None,
    priority: int | None = None,
    delay_seconds: float = 0.0,
    start_step_index: int = 0,
    force: bool = False,
    wake_worker: bool = True,
) -> dict[str, Any]:
    """Enqueue a DSL scenario for the worker instead of running it inline."""

    key = str(scenario_key or "").strip()
    if not key:
        msg = "scenario_key is required"
        raise ValueError(msg)
    iid = str(instance_id or "").strip()
    if not iid:
        msg = "instance_id is required"
        raise ValueError(msg)

    loaded = template_resolver.load_doc(_repo(), key)
    if loaded is None:
        msg = f"scenario not found: {key}"
        raise ValueError(msg)
    path, doc = loaded

    redis = await get_scheduler_async_redis()
    is_device_level = dsl_scenario_yaml_device_level(_repo(), key)
    if is_device_level:
        pid = ""
    else:
        pid = str(player_id or "").strip() or await _read_active_player(iid, redis)
        if not pid:
            msg = f"scenario {key!r} is player-bound; pass player_id or set active_player first"
            raise ValueError(
                msg
            )

    scen_priority = dsl_scenario_yaml_priority(_repo(), key)
    prio = int(priority if priority is not None else (scen_priority or DEFAULT_SCENARIO_PRIORITY))
    run_at = time.time() + max(0.0, float(delay_seconds))
    start_idx = max(0, int(start_step_index))
    task_id = f"mcp:push:{iid}:{key}:{uuid.uuid4().hex[:8]}"

    queue = await get_scheduler_queue()
    enqueued = await queue.schedule(
        task_id=task_id,
        player_id=pid,
        task_type=key,
        priority=prio,
        run_at=run_at,
        instance_id=iid,
        start_step_index=start_idx,
        skip_if_duplicate=not bool(force),
        dedup_ignore_region=True,
    )

    if enqueued and wake_worker:
        await redis.lpush(
            f"wos:ui:command:{iid}",
            json.dumps({"cmd": "wake", "source": "mcp.push_scenario", "scenario": key}),
        )

    return {
        "instance_id": iid,
        "scenario": key,
        "name": str(doc.get("name") or ""),
        "enabled": bool(doc.get("enabled", False)),
        "scenario_path": path.relative_to(_repo()).as_posix(),
        "task_id": task_id if enqueued else None,
        "queued": bool(enqueued),
        "duplicate_skipped": not bool(enqueued),
        "player_id": pid,
        "device_level": bool(is_device_level),
        "priority": prio,
        "run_at": run_at,
        "delay_seconds": max(0.0, float(delay_seconds)),
        "start_step_index": start_idx,
        "force": bool(force),
        "worker_woken": bool(enqueued and wake_worker),
    }


@mcp.tool()
def reference_normalize_basename(
    basename: str,
    instance_id: str = "",
) -> dict[str, Any]:
    """Sanitize a reference PNG basename (same rules as Labeling **Basename** field).

    Returns the stem without ``.png``. Pass ``instance_id`` only when you want
    rolling-preview defaults for empty input (usually leave it empty for module refs).
    """

    raw = str(basename or "").strip()
    if not raw:
        msg = "basename is required"
        raise ValueError(msg)
    normalized = normalize_reference_basename(raw, instance_id)
    return {
        "input": raw,
        "basename": normalized,
        "filename": f"{normalized}.png",
        "instance_id": str(instance_id or ""),
    }


@mcp.tool()
def reference_suggest_basename(
    source: str,
    instance_id: str = "",
    screen_id: str | None = None,
) -> dict[str, Any]:
    """Suggest a stable basename from ``area.yaml`` / ``area.json`` for a reference PNG.

    ``source`` is repo-relative, e.g. ``modules/core/shop/references/page.shop.v1.png``.
    """

    rel = str(source or "").strip()
    if not rel:
        msg = "source is required (repo-relative path to the .png)"
        raise ValueError(msg)
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        msg = "source must be a repo-relative path without '..'"
        raise ValueError(msg)
    return suggest_reference_basename(
        _repo(),
        source_repo_rel=rel,
        instance_id=instance_id,
        screen_id=screen_id,
    )


@mcp.tool()
def reference_rename_basename(
    source: str,
    basename: str,
    instance_id: str = "",
    sync_area: bool = True,
    rename_crops: bool = True,
) -> dict[str, Any]:
    """Rename a reference screenshot and sync ``area`` ``ocr`` paths (and crops).

    Example::

        reference_rename_basename(
            source="modules/core/shop/references/page.shop.v1.png",
            basename="page.shop.v2",
        )

    Rolls back the PNG rename if ``area`` sync fails when ``sync_area`` is true.
    """

    rel = str(source or "").strip()
    raw_bn = str(basename or "").strip()
    if not rel:
        msg = "source is required (repo-relative path to the .png)"
        raise ValueError(msg)
    if not raw_bn:
        msg = "basename is required (new filename without .png)"
        raise ValueError(msg)
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        msg = "source must be a repo-relative path without '..'"
        raise ValueError(msg)
    return rename_reference_basename(
        _repo(),
        source_repo_rel=rel,
        basename=raw_bn,
        instance_id=instance_id,
        sync_area=bool(sync_area),
        rename_crops=bool(rename_crops),
    )


@mcp.tool()
async def run_scenario_from_step(
    scenario_key: str,
    start_step_index: int = 0,
    instance_id: str = "bs1",
    player_id: str | None = None,
) -> dict[str, Any]:
    """Run a DSL scenario through DslScenarioTask, optionally starting at a top-level step."""

    redis = await get_scheduler_async_redis()
    pid = str(player_id or "").strip() or await _read_active_player(instance_id, redis) or "mcp"
    task = DslScenarioTask(
        task_id=f"mcp-run:{scenario_key}:{int(start_step_index)}",
        player_id=pid,
        scenario_key=scenario_key,
        start_step_index=max(0, int(start_step_index)),
        redis_client=redis,
    )
    result = await task.execute(instance_id)
    return {
        "instance_id": instance_id,
        "scenario": scenario_key,
        "success": bool(result.success),
        "next_run_at": result.next_run_at.isoformat() if result.next_run_at else None,
        "metadata": result.metadata,
    }


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
