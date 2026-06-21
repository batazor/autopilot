from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from conftest import make_actions

import tasks.dsl_exec as dsl_exec
from tasks import dsl_runtime
from tasks.dsl_exec import red_dots


class _FakeImage:
    shape = (1280, 720, 3)


def _recording_actions(image: Any | None = None) -> Any:
    taps: list[tuple[int, int]] = []
    actions = make_actions(resolution=(720, 1280))
    actions.capture_screen_bgr.return_value = image if image is not None else _FakeImage()

    def _tap(_instance_id: str, point: Any, **_kwargs: object) -> bool:
        taps.append((point.x, point.y))
        return True

    actions.tap.side_effect = _tap
    actions._test_taps = taps  # type: ignore[attr-defined]
    return actions


@pytest.mark.asyncio
async def test_put_all_red_dots_cycle_guard_filters_stuck_dot(
    mocker,
    redis_async: object,
) -> None:
    """Stuck dot (popup re-opens onto same spot) is tapped at most ``_DUP_MAX_HITS``
    times, then the area joins the sweep-local filter and the next detection in
    the radius is dropped — letting the function exit on ``dots == []`` instead
    of grinding to the global ``_MAX_TAPS`` cap.
    """

    actions = _recording_actions()
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(red_dots, "_PUT_ALL_RED_DOTS_TAP_DELAY_S", 0)
    mocker.patch.object(red_dots, "_PUT_ALL_RED_DOTS_RESCAN_DELAY_S", 0)

    jitter = iter([(400, 600), (402, 601), (399, 603), (401, 598), (400, 600)])

    def _fake_find_red_dots(_image: Any, image_h_for_norm: int) -> list[Any]:
        del image_h_for_norm
        try:
            cx, cy = next(jitter)
        except StopIteration:
            return []
        return [SimpleNamespace(cx=float(cx), cy=float(cy), radius=8.0, score=0.9)]

    mocker.patch.object(red_dots, "find_red_dots", _fake_find_red_dots)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    taps = actions._test_taps  # type: ignore[attr-defined]
    assert len(taps) == red_dots._PUT_ALL_RED_DOTS_DUP_MAX_HITS, (
        f"expected exactly {red_dots._PUT_ALL_RED_DOTS_DUP_MAX_HITS} taps "
        f"before the cycle guard banned the area, got {taps!r}"
    )
    for tx, ty in taps:
        assert abs(tx - 400) <= red_dots._PUT_ALL_RED_DOTS_DUP_RADIUS_PX
        assert abs(ty - 600) <= red_dots._PUT_ALL_RED_DOTS_DUP_RADIUS_PX


@pytest.mark.asyncio
async def test_put_all_red_dots_distinct_spots_are_not_filtered(
    mocker,
    redis_async: object,
) -> None:
    """Sanity check: dots farther apart than the dedup radius never join the
    filter and are tapped normally until the frame clears.
    """

    actions = _recording_actions()
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(red_dots, "_PUT_ALL_RED_DOTS_TAP_DELAY_S", 0)
    mocker.patch.object(red_dots, "_PUT_ALL_RED_DOTS_RESCAN_DELAY_S", 0)

    frames = iter(
        [
            [SimpleNamespace(cx=100.0, cy=200.0, radius=8.0, score=0.9)],
            [SimpleNamespace(cx=500.0, cy=900.0, radius=8.0, score=0.9)],
            [],
        ]
    )

    def _fake_find_red_dots(_image: Any, image_h_for_norm: int) -> list[Any]:
        del image_h_for_norm
        return next(frames, [])

    mocker.patch.object(red_dots, "find_red_dots", _fake_find_red_dots)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    taps = actions._test_taps  # type: ignore[attr-defined]
    assert taps == [(100, 200), (500, 900)]


@pytest.mark.asyncio
async def test_put_all_red_dots_region_arg_crops_search_and_translates_coords(
    mocker,
    redis_async: object,
) -> None:
    """With ``region:`` set, the search runs on the cropped patch and detector
    coordinates are translated back to absolute frame coords for tapping.
    """
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    actions = _recording_actions(image)
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(red_dots, "_PUT_ALL_RED_DOTS_TAP_DELAY_S", 0)
    mocker.patch.object(red_dots, "_PUT_ALL_RED_DOTS_RESCAN_DELAY_S", 0)
    mocker.patch.object(red_dots, "_load_area_doc", return_value={"_fake": True})
    mocker.patch.object(
        red_dots,
        "screen_region_by_name",
        lambda _doc, name: (
            {},
            {
                "name": name,
                "bbox": {"x": 50.0, "y": 50.0, "width": 10.0, "height": 10.0},
            },
        ),
    )

    received_patch_shapes: list[tuple[int, int]] = []
    frames = iter(
        [
            [SimpleNamespace(cx=10.0, cy=20.0, radius=8.0, score=0.9)],
            [],
        ]
    )

    def _fake_find_red_dots(patch: Any, image_h_for_norm: int) -> list[Any]:
        received_patch_shapes.append((patch.shape[0], patch.shape[1]))
        assert image_h_for_norm == 1280
        return next(frames, [])

    mocker.patch.object(red_dots, "find_red_dots", _fake_find_red_dots)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={"region": "page.heroes"},
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    assert received_patch_shapes[0] == (128, 72)
    taps = actions._test_taps  # type: ignore[attr-defined]
    assert taps == [(370, 660)]


@pytest.mark.asyncio
async def test_put_all_red_dots_region_arg_missing_region_aborts(
    mocker,
    redis_async: object,
) -> None:
    """Unknown ``region:`` short-circuits: no capture, no detection, no taps."""
    actions = _recording_actions()
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(red_dots, "_load_area_doc", return_value={"_fake": True})
    mocker.patch.object(red_dots, "screen_region_by_name", return_value=None)

    def _fail_find(*_a: Any, **_k: Any) -> list[Any]:
        msg = "find_red_dots must not run when region is unresolved"
        raise AssertionError(msg)

    mocker.patch.object(red_dots, "find_red_dots", _fail_find)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={"region": "does.not.exist"},
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    assert actions._test_taps == []  # type: ignore[attr-defined]
