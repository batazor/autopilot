from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
from mcp.server.fastmcp import FastMCP
from PIL import Image

from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point
from navigation.detector import ScreenName
from omniparser.client import check_omniparser_health, parse_screenshot
from omniparser.supervision_bridge import (
    build_omniparser_proposal_regions,
    parsed_element_to_dict,
)
from scenarios import template_resolver
from services import get_bot_actions, get_repo_root, get_scheduler_async_redis
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
    return json.loads((_repo() / "area.json").read_text(encoding="utf-8"))


def _safe_output_path(output: str | None, *, default_name: str) -> Path:
    rel = str(output or "").strip() or f"references/temporal/{default_name}"
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("output must be a repo-relative path without '..'")
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
        raise RuntimeError(f"failed to write screenshot: {path}")
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
        raise ValueError(f"unknown node {target_node!r}; add it to screen_verify.yaml first") from exc
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
        raise ValueError(f"scenario not found: {scenario_key}")
    path, doc = loaded
    steps = doc.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"scenario has no steps list: {scenario_key}")
    idx = int(step_index)
    if idx < 0 or idx >= len(steps):
        raise IndexError(f"step_index {idx} out of range for {scenario_key} ({len(steps)} steps)")
    step = steps[idx]
    if not isinstance(step, dict):
        raise ValueError(f"step {idx} is not a mapping")

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
        raise RuntimeError("current_screen is unknown; run detect_screen or pass screen_id")

    pair = screen_region_by_name(_area_doc(), region)
    if pair is None:
        raise ValueError(f"region {region!r} not found on screen {current!r}")
    _entry, reg = pair
    bbox = reg.get("bbox")
    if not isinstance(bbox, dict):
        raise ValueError(f"region {region!r} has no bbox")

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
