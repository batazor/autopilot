from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    tapped: list[tuple[str, int, int, str | None]] = []
    swipes: list[tuple[str, str, int, int]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 1000, 1000

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        return np.zeros((1000, 1000, 3), dtype=np.uint8)

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, int(point.x), int(point.y), approval_region))
        return True

    def swipe_direction(
        self, instance_id: str, *, direction: str, delta: int, duration_ms: int
    ) -> bool:
        self.swipes.append((instance_id, str(direction), int(delta), int(duration_ms)))
        return True


@pytest.mark.asyncio
async def test_dsl_break_repeat_stops_swipe(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios" / "mail").mkdir(parents=True)
    (tmp_path / "scenarios" / "mail" / "break_repeat.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Break repeat",
                "steps": [
                    {
                        "repeat": {
                            "max": 3,
                            "steps": [
                                {
                                    "while_match": "big_claim_button",
                                    "max": 1,
                                    "steps": [
                                        {"click": "big_claim_button"},
                                        {"break": "repeat"},
                                    ],
                                },
                                {
                                    "swipe_direction": {
                                        "direction": "down",
                                        "delta": 450,
                                        "duration_ms": 320,
                                    }
                                },
                            ],
                        }
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
                        "ocr": "references/mail.png",
                        "regions": [
                            {
                                "name": "big_claim_button",
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                                "threshold": 0.9,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def _fake_eval(_image_bgr: Any, _area_doc: Any, _repo_root: Any, rules: Any, **_kw: Any) -> Any:
        # DslScenarioTask passes a single rule with name `dsl.<scenario>.<region>.visible`
        assert isinstance(rules, list) and rules
        name = str(rules[0].get("name") or "")
        return {
            name: {
                "matched": True,
                "score": 0.99,
                "threshold": 0.9,
                "tap_x_pct": 50.0,
                "tap_y_pct": 60.0,
                "tap_match_x_pct": 50.0,
                "tap_match_y_pct": 60.0,
                "top_left": [100, 200],
                "template_w": 240,
                "template_h": 58,
                "search_region": "big_claim_button_search",
            }
        }

    fake_actions = _FakeActions()
    fake_actions.tapped = []
    fake_actions.swipes = []
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: fake_actions)
    monkeypatch.setattr(dsl, "evaluate_overlay_rules_async", _fake_eval)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="break_repeat",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")
    assert result.success is True
    assert fake_actions.tapped  # click happened
    assert fake_actions.swipes == []  # break stopped repeat before swipe

