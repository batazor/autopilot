"""End-to-end coverage for the ``isRedDot:`` filter on DSL ``match:`` steps.

Three scenarios:

* ``isRedDot: true`` + dot present → standard click happens.
* ``isRedDot: true`` + dot absent → click skipped (``red_dot_missing``).
* ``isRedDot: true`` on a region without ``has_red_dot: true`` capability →
  guard fails with ``red_dot_capability_disabled`` (typo / mis-config safety).

Plus pure unit checks for the static parser and post-filter so we can iterate
on edge-cases without spinning up Redis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import cv2
import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Pure helpers — no Redis, no asyncio
# ---------------------------------------------------------------------------


def test_step_red_dot_requirement_reads_bool_only() -> None:
    assert dsl._step_red_dot_requirement({"isRedDot": True}) is True
    assert dsl._step_red_dot_requirement({"isRedDot": False}) is False
    assert dsl._step_red_dot_requirement({}) is None
    assert dsl._step_red_dot_requirement({"isRedDot": "yes"}) is None


def _frame_with_red_dot(w: int = 720, h: int = 1280, *, with_dot: bool) -> np.ndarray:
    """Calibration-sized frame (game-typical 720×1280) so the detector's radius
    range covers the synthetic dot without re-tuning constants per-test.

    Background is a saturated dark teal (BGR 90,60,30 → HSV S≈170) so the
    detector's surround-saturation gate sees a button-like surface around the
    synthetic dot. Pure grey would trip the gate as a false negative."""
    img = np.full((h, w, 3), (90, 60, 30), dtype=np.uint8)
    if with_dot:
        cv2.circle(img, (w // 2, h // 2), 10, (40, 40, 230), thickness=-1)
    return img


def test_build_red_dot_only_row_matches_when_dot_present() -> None:
    region_def = {
        "name": "mailBox",
        "has_red_dot": True,
        "bbox": {"x": 40.0, "y": 40.0, "width": 20.0, "height": 20.0},
    }
    out = dsl.DslScenarioTask._build_red_dot_only_row(
        region="mailBox",
        region_def=region_def,
        image_bgr=_frame_with_red_dot(with_dot=True),
        requirement=True,
    )
    assert out["matched"] is True
    assert out["red_dot_present"] is True
    assert out["red_dot_required"] is True
    # Tap point falls back to bbox center when red-dot path matches.
    assert out["tap_x_pct"] == 50.0
    assert out["tap_y_pct"] == 50.0


def test_build_red_dot_only_row_misses_when_dot_absent() -> None:
    region_def = {
        "name": "mailBox",
        "has_red_dot": True,
        "bbox": {"x": 40.0, "y": 40.0, "width": 20.0, "height": 20.0},
    }
    out = dsl.DslScenarioTask._build_red_dot_only_row(
        region="mailBox",
        region_def=region_def,
        image_bgr=_frame_with_red_dot(with_dot=False),
        requirement=True,
    )
    assert out["matched"] is False
    assert out["red_dot_present"] is False
    assert out["reason"] == "red_dot_missing"


def test_build_red_dot_only_row_misses_when_dot_unexpectedly_present() -> None:
    region_def = {
        "name": "mailBox",
        "has_red_dot": True,
        "bbox": {"x": 40.0, "y": 40.0, "width": 20.0, "height": 20.0},
    }
    out = dsl.DslScenarioTask._build_red_dot_only_row(
        region="mailBox",
        region_def=region_def,
        image_bgr=_frame_with_red_dot(with_dot=True),
        requirement=False,
    )
    assert out["matched"] is False
    assert out["reason"] == "red_dot_unexpected"


def test_build_red_dot_only_row_errors_without_capability_flag() -> None:
    region_def = {
        "name": "mailBox",
        "bbox": {"x": 40.0, "y": 40.0, "width": 20.0, "height": 20.0},
    }
    out = dsl.DslScenarioTask._build_red_dot_only_row(
        region="mailBox",
        region_def=region_def,
        image_bgr=_frame_with_red_dot(with_dot=True),
        requirement=True,
    )
    assert out["matched"] is False
    assert out["reason"] == "red_dot_capability_disabled"


# ---------------------------------------------------------------------------
# End-to-end DSL execution
# ---------------------------------------------------------------------------


def _write_red_dot_repo(
    tmp_path: Path,
    frame: np.ndarray,
    *,
    has_red_dot: bool,
    is_red_dot_step: bool,
) -> None:
    """Write a tiny module/area.json/crop layout that exercises ``isRedDot``.

    Region ``mailBox`` matches via 1:1 template (same way real ``exist`` matches),
    optionally flagged with ``has_red_dot``. The scenario taps the region only when
    ``isRedDot`` requirement holds.
    """
    module = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "events"
    (module / "scenarios" / "events").mkdir(parents=True)
    (module / "module.yaml").write_text("id: events\n", encoding="utf-8")
    (tmp_path / "references" / "crop").mkdir(parents=True)

    steps: list[dict[str, Any]] = [{"match": "mailBox", "threshold": 0.95}]
    if is_red_dot_step:
        steps[0]["isRedDot"] = True
    steps.append({"click": "mailBox"})

    (module / "scenarios" / "events" / "open_mail.yaml").write_text(
        yaml.dump({"enabled": True, "name": "OpenMail", "steps": steps}),
        encoding="utf-8",
    )

    bbox = {"x": 30.0, "y": 30.0, "width": 40.0, "height": 40.0}
    hf, wf = frame.shape[:2]
    px = int(bbox["x"] / 100.0 * wf)
    py = int(bbox["y"] / 100.0 * hf)
    pw = int(bbox["width"] / 100.0 * wf)
    ph = int(bbox["height"] / 100.0 * hf)
    crop = frame[py : py + ph, px : px + pw]
    cv2.imwrite(str(tmp_path / "references/crop/mailbox_screen_mailBox.png"), crop)

    region: dict[str, Any] = {
        "name": "mailBox",
        "action": "exist",
        "type": "string",
        "threshold": 0.9,
        "bbox": bbox,
    }
    if has_red_dot:
        region["has_red_dot"] = True

    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/mailbox_screen.png",
                        "regions": [region],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _mailbox_frame(*, with_red_dot: bool, w: int = 720, h: int = 1280) -> np.ndarray:
    """Stable, distinctive non-red template patch + an optional red dot inside it.

    Sized to the game-typical 720×1280 framebuffer so the detector's pixel-radius
    range (calibrated for ``REFERENCE_IMAGE_HEIGHT``) accepts the synthetic dot.
    """
    img = np.full((h, w, 3), 32, dtype=np.uint8)
    box_x0, box_y0 = int(0.30 * w), int(0.30 * h)
    box_x1, box_y1 = int(0.70 * w), int(0.70 * h)
    cv2.rectangle(img, (box_x0, box_y0), (box_x1, box_y1), (180, 180, 60), thickness=-1)
    cv2.rectangle(
        img,
        (box_x0 + 8, box_y0 + 8),
        (box_x1 - 8, box_y1 - 8),
        (220, 220, 220),
        thickness=3,
    )
    if with_red_dot:
        cv2.circle(img, (box_x1 - 24, box_y0 + 24), 10, (40, 40, 230), thickness=-1)
    return img


@pytest.mark.asyncio
async def test_dsl_is_red_dot_true_clicks_when_dot_present(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    frame = _mailbox_frame(with_red_dot=True)
    _write_red_dot_repo(tmp_path, frame, has_red_dot=True, is_red_dot_step=True)
    actions = make_actions(frame)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="open_mail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert len(actions.tap.call_args_list) == 1


@pytest.mark.asyncio
async def test_dsl_is_red_dot_true_skips_click_when_dot_absent(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    frame = _mailbox_frame(with_red_dot=False)
    _write_red_dot_repo(tmp_path, frame, has_red_dot=True, is_red_dot_step=True)
    actions = make_actions(frame)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="open_mail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    # Failed guard is reported as ``success=False`` (queue history honesty).
    assert result.success is False
    assert result.metadata["reason"] == "match_guard_failed"
    assert actions.tap.call_args_list == []
    match_row = result.metadata.get("match")
    assert isinstance(match_row, dict)
    match_dict: dict[str, Any] = cast("dict[str, Any]", match_row)
    assert match_dict.get("reason") == "red_dot_missing"


@pytest.mark.asyncio
async def test_dsl_is_red_dot_without_capability_flag_fails_guard(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    frame = _mailbox_frame(with_red_dot=True)
    _write_red_dot_repo(tmp_path, frame, has_red_dot=False, is_red_dot_step=True)
    actions = make_actions(frame)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="open_mail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "match_guard_failed"
    assert actions.tap.call_args_list == []
    match_row = result.metadata.get("match")
    assert isinstance(match_row, dict)
    match_dict: dict[str, Any] = cast("dict[str, Any]", match_row)
    assert match_dict.get("reason") == "red_dot_capability_disabled"
