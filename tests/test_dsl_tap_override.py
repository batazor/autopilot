from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeRedis:
    async def hset(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def hget(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeActions:
    tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 1000, 1000

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


@pytest.mark.asyncio
async def test_dsl_click_uses_overlay_tap_override(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "hand_pointer.yaml").write_text(
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

    fake_actions = _FakeActions()
    fake_actions.tapped = []
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: fake_actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="hand_pointer",
        redis_client=_FakeRedis(),
        tap_region="hand_pointer",
        tap_x_pct=50.0,
        tap_y_pct=70.0,
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert fake_actions.tapped == [("bs1", 500, 700, "hand_pointer")]
