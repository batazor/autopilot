from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl
from conftest import patch_dsl_bot_actions
from scenarios import template_resolver

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[1]


class _FakeActions:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = frames
        self.capture_count = 0
        self.tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 720, 1280

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        idx = min(self.capture_count, len(self.frames) - 1)
        self.capture_count += 1
        return self.frames[idx]

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _region_bbox(region_name: str) -> dict[str, float]:
    area_doc = yaml.safe_load((REPO_ROOT / "area.json").read_text(encoding="utf-8"))
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == region_name:
                return region["bbox"]
    raise AssertionError(f"missing region {region_name!r}")


def _draw_red_dot(frame: np.ndarray, region_name: str) -> None:
    bbox = _region_bbox(region_name)
    width = frame.shape[1]
    height = frame.shape[0]
    x0 = int(width * float(bbox["x"]) / 100)
    y0 = int(height * float(bbox["y"]) / 100)
    w = int(width * float(bbox["width"]) / 100)
    h = int(height * float(bbox["height"]) / 100)
    cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), (255, 128, 0), -1)
    center = (x0 + max(6, w // 2), y0 + max(6, h // 2))
    radius = 10
    cv2.circle(frame, center, radius, (0, 0, 255), -1)
    cv2.circle(frame, center, max(3, radius // 3), (255, 255, 255), -1)


def test_vip_daily_scenario_is_registered_with_expected_shape() -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "vip.daily")
    assert loaded is not None

    path, doc = loaded
    assert path == MODULE_DIR / "scenarios" / "by_cron" / "vip.daily.yaml"
    assert doc["enabled"] is True
    assert doc["node"] == "vip"
    assert doc["cron"] == "0 */6 * * *"

    guards = doc["steps"]
    assert [step["while_match"] for step in guards] == ["page.vip.box", "page.vip.add"]
    assert all(step.get("isRedDot") is True for step in guards)
    assert all(step.get("max") == 1 for step in guards)


@pytest.mark.asyncio
async def test_vip_daily_scenario_clicks_claimable_vip_box(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "vip"},
    )

    visible = np.zeros((1280, 720, 3), dtype=np.uint8)
    _draw_red_dot(visible, "page.vip.box")
    blank = np.zeros((1280, 720, 3), dtype=np.uint8)

    actions = _FakeActions([visible, blank])
    monkeypatch.setattr(dsl, "_repo_root", lambda: REPO_ROOT)
    patch_dsl_bot_actions(monkeypatch, actions)

    task = dsl.DslScenarioTask(
        task_id="vip-daily-test",
        player_id="p1",
        scenario_key="vip.daily",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tapped == [("bs1", 630, 275, "page.vip.box")]
