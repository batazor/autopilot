"""``exec: drain_use_all`` — spends whole stacks via the ×N pill, gated taps."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import pytest
from conftest import make_actions

from tasks import dsl_runtime
from tasks.dsl_exec.context import DslExecContext
from tasks.dsl_exec.use_all import _exec_drain_use_all

REPO_ROOT = Path(__file__).resolve().parents[2]
INCREASE_LEVEL = REPO_ROOT / "games" / "wos" / "vip" / "references" / "increase_level.png"


def _frames() -> list[Any]:
    """increase_level → row1 pill gone → both pills + Use gone."""
    f0 = cv2.imread(str(INCREASE_LEVEL))
    assert f0 is not None
    # Clear the row-1 ×N pill (button.use_all template area) — only ×55 remains.
    f1 = f0.copy()
    cv2.rectangle(f1, (290, 340), (470, 420), (60, 60, 60), -1)
    # Clear both pill + Use columns for both rows — nothing left to drain.
    f2 = f1.copy()
    cv2.rectangle(f2, (290, 340), (675, 560), (60, 60, 60), -1)
    return [f0, f1, f2]


@pytest.mark.asyncio
async def test_drain_use_all_spends_every_pill_then_stops(mocker) -> None:
    frames = _frames()
    actions = make_actions(frames)

    taps: list[tuple[int, int, str]] = []

    def _tap(_iid: str, point: Any, *, approval_region: str = "", **_k: object) -> bool:
        taps.append((point.x, point.y, approval_region))
        return True

    actions.tap.side_effect = _tap
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)

    ctx = DslExecContext(
        redis_client=None,
        player_id="p1",
        instance_id="bs1",
        args={"settle_ms": 0},
    )
    await _exec_drain_use_all(ctx)

    # Two distinct ×N pills drained (row1 then row2), then the search dries up.
    assert ctx.result["use_all_taps"] == 2, ctx.result
    assert ctx.result["use_taps"] == 0, ctx.result
    assert [r for *_xy, r in taps] == ["button.use_all", "button.use_all"]
    # Both taps land in the pill column (x≈335), not on the Use button (x≈575).
    assert all(280 < x < 380 for x, _y, _r in taps), taps
    # Distinct rows: the second tap is lower than the first.
    assert taps[1][1] > taps[0][1], taps


@pytest.mark.asyncio
async def test_drain_use_all_no_popup_is_a_noop(mocker) -> None:
    """No pill / no Use on screen → zero taps, scenario continues."""
    import numpy as np

    blank = np.zeros((1280, 720, 3), dtype=np.uint8)
    actions = make_actions([blank])
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)

    ctx = DslExecContext(
        redis_client=None, player_id="p1", instance_id="bs1", args={"settle_ms": 0}
    )
    await _exec_drain_use_all(ctx)

    assert ctx.result == {
        "action": "drained",
        "use_all_region": "button.use_all",
        "use_region": "button.use",
        "use_all_taps": 0,
        "use_taps": 0,
    }
    actions.tap.assert_not_called()
