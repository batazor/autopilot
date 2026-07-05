"""Repro attempt: live bs6 who_i_am stored a 2-digit player_id past min_digits: 7.

Observed live (2026-07-06, RU build): the scenario trace showed
``ocr:player.id → text '16' / confidence 0.0 / status stored`` and the run
finished ok=True. The production step is exactly:

    - ocr: player.id
      store: player_id
      type: integer
      preprocess: fast_digits
      threshold: 0.0
      min_digits: 7

This test replays that exact step (same threshold/preprocess/min_digits
combination — existing coverage used default thresholds and high confidence)
with a stub OCR returning the live values. The gate must reject the store and
must NOT bind active_player.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from ocr.client import OCRResult

if TYPE_CHECKING:
    from pathlib import Path

    from layout.types import Region as LayoutRegion


def _write_repo(tmp_path: Path) -> None:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "who_i_am.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Who am I (prod step copy)",
                "device_level": True,
                "steps": [
                    {
                        "ocr": "player.id",
                        "store": "player_id",
                        "type": "integer",
                        "preprocess": "fast_digits",
                        "threshold": 0.0,
                        "min_digits": 7,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 18,
                        "screen_id": "chief_profile",
                        "ocr": "references/chief_profile.png",
                        "regions": [
                            {
                                "name": "player.id",
                                "action": "text",
                                "type": "integer",
                                "threshold": 0.5,
                                "bbox": {
                                    "x": 25.0,
                                    "y": 50.0,
                                    "width": 50.0,
                                    "height": 10.0,
                                },
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_prod_step_rejects_two_digit_id_at_zero_confidence(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _LiveValuesStub:
        async def ocr_region(
            self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
        ) -> OCRResult:
            # fast_digits collapses word confidence to ~0 even on clean reads —
            # threshold: 0.0 deliberately floors it, so min_digits is the gate.
            return OCRResult(region_id="r0", text="16", confidence=0.0)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _LiveValuesStub)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-min-digits-prod",
        player_id="",
        scenario_key="who_i_am",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    ap = await redis_async.hget("wos:instance:bs1:state", "active_player")  # type: ignore[attr-defined]
    assert ap in {None, ""}, f"2-digit OCR junk must never bind identity, got {ap!r}"
    last = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]
    assert last.get("dsl_last_ocr_status") == "integer_too_short", (
        f"expected integer_too_short, got {last.get('dsl_last_ocr_status')!r} "
        f"(result={result.metadata.get('reason')!r})"
    )
