from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 1000, 1000

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _scenario_root(tmp_path: Path) -> Path:
    mod = tmp_path / "modules" / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


@pytest.mark.asyncio
async def test_dsl_click_uses_overlay_tap_override(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "hand_pointer.yaml").write_text(
        yaml.dump({"enabled": True, "name": "Hand", "steps": [{"click": "hand_pointer"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/tutorial.png",
                        "regions": [
                            {
                                "name": "hand_pointer",
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    fake_actions = make_actions()
    fake_actions.tapped = []
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: fake_actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="hand_pointer",
        redis_client=redis_async,  # type: ignore[arg-type]
        tap_region="hand_pointer",
        tap_x_pct=50.0,
        tap_y_pct=70.0,
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert fake_actions.tapped == [("bs1", 500, 700, "hand_pointer")]


@pytest.mark.asyncio
async def test_dsl_click_forwards_min_match_saturation_to_implicit_match(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """``click: foo / min_match_saturation: 40`` reaches the implicit search rule.

    Without forwarding, click steps with `_search` companions would fall back to
    the engine default and ignore user-set saturation gates.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "main_city").mkdir(parents=True)
    (scenario_root / "main_city" / "tap_with_sat.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Tap with saturation",
                "device_level": True,
                "steps": [
                    {
                        "click": "claim_btn",
                        "min_match_saturation": 40,
                        "threshold": 0.97,
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
                        "id": 1,
                        "ocr": "references/x.png",
                        "regions": [
                            {
                                "name": "claim_btn",
                                "action": "exist",
                                "type": "string",
                                "threshold": 0.9,
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            },
                            # Search companion → triggers implicit match-on-click.
                            {
                                "name": "claim_btn_search",
                                "action": "exist",
                                "type": "string",
                                "threshold": 0.9,
                                "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                                "overlay_auxiliary": True,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    captured_rule: dict[str, Any] = {}

    async def _fake_eval(
        _image_bgr: Any, _area_doc: Any, _repo_root: Any, rules: Any, **_kw: Any
    ) -> Any:
        # Stash the rule the engine sees so the test can inspect forwarded keys.
        captured_rule.clear()
        captured_rule.update(rules[0])
        name = str(rules[0].get("name") or "")
        return {
            name: {
                "matched": True,
                "score": 0.99,
                "threshold": 0.97,
                "tap_x_pct": 50.0,
                "tap_y_pct": 60.0,
                "top_left": [10, 10],
                "template_w": 50,
                "template_h": 50,
            }
        }

    import numpy as np

    class _FakeActionsWithCapture:
        def __init__(self) -> None:
            self.tapped: list[tuple[str, int, int, str | None]] = []

        def screen_resolution(self, instance_id: str) -> tuple[int, int]:
            return 1000, 1000

        def capture_screen_bgr(self, instance_id: str) -> Any:
            return np.zeros((1000, 1000, 3), dtype=np.uint8)

        def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
            self.tapped.append((instance_id, point.x, point.y, approval_region))
            return True

    actions = _FakeActionsWithCapture()
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl, "evaluate_overlay_rules_async", _fake_eval)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_with_sat",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")
    assert result.success is True
    assert captured_rule.get("min_match_saturation") == 40
    assert captured_rule.get("threshold") == pytest.approx(0.97)
