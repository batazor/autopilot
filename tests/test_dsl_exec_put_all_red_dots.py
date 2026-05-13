from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import tasks.dsl_exec as dsl_exec


class _FakeImage:
    shape = (1280, 720, 3)


class _RecordingActions:
    def __init__(self, image: Any = None) -> None:
        self.taps: list[tuple[int, int]] = []
        self._image = image if image is not None else _FakeImage()

    def capture_screen_bgr(self, *_args: Any, **_kwargs: Any) -> Any:
        return self._image

    def tap(self, _instance_id: str, point: Any) -> bool:
        self.taps.append((point.x, point.y))
        return True


@pytest.mark.asyncio
async def test_put_all_red_dots_cycle_guard_filters_stuck_dot(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
) -> None:
    """Stuck dot (popup re-opens onto same spot) is tapped at most ``_DUP_MAX_HITS``
    times, then the area joins the sweep-local filter and the next detection in
    the radius is dropped — letting the function exit on ``dots == []`` instead
    of grinding to the global ``_MAX_TAPS`` cap.
    """

    actions = _RecordingActions()
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "_PUT_ALL_RED_DOTS_TAP_DELAY_S", 0)
    monkeypatch.setattr(dsl_exec, "_PUT_ALL_RED_DOTS_RESCAN_DELAY_S", 0)

    # Simulate the detector returning the same dot every frame with sub-5px
    # jitter — exactly the runaway loop the guard is meant to break.
    jitter = iter([(400, 600), (402, 601), (399, 603), (401, 598), (400, 600)])

    def _fake_find_red_dots(_image: Any, image_h_for_norm: int) -> list[Any]:
        try:
            cx, cy = next(jitter)
        except StopIteration:
            return []
        return [SimpleNamespace(cx=float(cx), cy=float(cy), radius=8.0, score=0.9)]

    monkeypatch.setattr(dsl_exec, "find_red_dots", _fake_find_red_dots)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    assert len(actions.taps) == dsl_exec._PUT_ALL_RED_DOTS_DUP_MAX_HITS, (
        f"expected exactly {dsl_exec._PUT_ALL_RED_DOTS_DUP_MAX_HITS} taps "
        f"before the cycle guard banned the area, got {actions.taps!r}"
    )
    for tx, ty in actions.taps:
        assert abs(tx - 400) <= dsl_exec._PUT_ALL_RED_DOTS_DUP_RADIUS_PX
        assert abs(ty - 600) <= dsl_exec._PUT_ALL_RED_DOTS_DUP_RADIUS_PX


@pytest.mark.asyncio
async def test_put_all_red_dots_distinct_spots_are_not_filtered(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
) -> None:
    """Sanity check: dots farther apart than the dedup radius never join the
    filter and are tapped normally until the frame clears.
    """

    actions = _RecordingActions()
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "_PUT_ALL_RED_DOTS_TAP_DELAY_S", 0)
    monkeypatch.setattr(dsl_exec, "_PUT_ALL_RED_DOTS_RESCAN_DELAY_S", 0)

    frames = iter(
        [
            [SimpleNamespace(cx=100.0, cy=200.0, radius=8.0, score=0.9)],
            [SimpleNamespace(cx=500.0, cy=900.0, radius=8.0, score=0.9)],
            [],
        ]
    )

    def _fake_find_red_dots(_image: Any, image_h_for_norm: int) -> list[Any]:
        return next(frames, [])

    monkeypatch.setattr(dsl_exec, "find_red_dots", _fake_find_red_dots)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    assert actions.taps == [(100, 200), (500, 900)]


@pytest.mark.asyncio
async def test_put_all_red_dots_region_arg_crops_search_and_translates_coords(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
) -> None:
    """With ``region:`` set, the search runs on the cropped patch and detector
    coordinates are translated back to absolute frame coords for tapping.
    """

    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    actions = _RecordingActions(image=image)
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "_PUT_ALL_RED_DOTS_TAP_DELAY_S", 0)
    monkeypatch.setattr(dsl_exec, "_PUT_ALL_RED_DOTS_RESCAN_DELAY_S", 0)

    # bbox at 50% origin, 10% size → pixel rect (360, 640) 72x128 on a 720x1280 frame.
    monkeypatch.setattr(
        dsl_exec,
        "_load_area_doc",
        lambda: {"_fake": True},
    )
    monkeypatch.setattr(
        dsl_exec,
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
        # image_h_for_norm must stay at full-screen height (1280), not crop height,
        # so radius bounds match the un-cropped scale.
        assert image_h_for_norm == 1280
        return next(frames, [])

    monkeypatch.setattr(dsl_exec, "find_red_dots", _fake_find_red_dots)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={"region": "page.heroes"},
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    # Crop shape: 128 rows × 72 cols.
    assert received_patch_shapes[0] == (128, 72)
    # Tap coords are absolute: crop origin (360, 640) + local dot (10, 20).
    assert actions.taps == [(370, 660)]


@pytest.mark.asyncio
async def test_put_all_red_dots_region_arg_missing_region_aborts(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
) -> None:
    """Unknown ``region:`` short-circuits: no capture, no detection, no taps."""

    actions = _RecordingActions()
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "_load_area_doc", lambda: {"_fake": True})
    monkeypatch.setattr(dsl_exec, "screen_region_by_name", lambda *_a, **_k: None)

    def _fail_find(*_a: Any, **_k: Any) -> list[Any]:
        raise AssertionError("find_red_dots must not run when region is unresolved")

    monkeypatch.setattr(dsl_exec, "find_red_dots", _fail_find)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={"region": "does.not.exist"},
    )

    await dsl_exec.DSL_EXEC_REGISTRY["put_all_red_dots"](ctx)

    assert actions.taps == []
