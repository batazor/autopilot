"""``type: time`` coercion for OCR'd HH:MM:SS / MM:SS values.

The DSL ``ocr`` step accepts a ``type:`` hint that controls how the raw OCR
text is normalized before persisting. This file covers the ``time`` variant —
"00:01:23" / "1:23:45" → total seconds (``int``) — both as a unit test of the
helper and as a round-trip through ``_persist_ocr_result``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import config.state_store as state_store_module
import tasks.dsl_ocr_mixin as dsl_ocr_mixin
import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from config.state_sqlite import set_state_db_path_for_tests
from config.state_store import get_state_store
from layout.types import Region as LayoutRegion
from ocr.client import OCRResult
from tasks.dsl_scenario_helpers import _parse_hms_to_seconds


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("00:00:00", 0),
        ("00:01:23", 83),
        ("01:23:45", 5025),
        ("00:30", 30),  # MM:SS form
        ("12:34", 754),
        ("  00:01:23  ", 83),  # whitespace tolerant
        ("Time: 00:01:23 left", 83),  # surrounding noise
        ("120:00:00", 432000),  # multi-day timer (5 days), no cap
        ("1d 09:11:19", 119479),
        ("Reset in 1 day 09:11:19", 119479),
        # Exact OCR output recorded against ``references/building.upgrading.png``
        # bbox ``building.upgrading.time`` (x=72.16% y=55.43% w=15.4% h=2.7%).
        # Locks in the round-trip the production overlay rule relies on for
        # ``throttle_push: building.upgrade`` (see analyze_building.yaml and
        # modules/core/building/common/scenarios/building.upgrade.yaml).
        ("00:00:27", 27),
    ],
)
def test_parse_hms_valid(text: str, expected: int) -> None:
    assert _parse_hms_to_seconds(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "abc",
        "1:60:00",  # minutes overflow
        "0:00:99",  # seconds overflow
        "12",       # bare integer — not a time
        "1:2",      # seconds field needs 2 digits, prevents collision with scores
    ],
)
def test_parse_hms_invalid(text: object) -> None:
    assert _parse_hms_to_seconds(text) is None  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def _scenario_root(tmp_path: Path) -> Path:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


def _write_timer_repo(tmp_path: Path) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "exploration").mkdir(parents=True)
    (scenario_root / "exploration" / "read_timer.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Read exploration timer",
                "device_level": True,
                "steps": [
                    # ``type: time`` lives on the step here so the test
                    # doesn't need a custom area.json entry; area-level
                    # default is exercised via region_def.get("type") in
                    # the implementation.
                    {
                        "ocr": "exploration_timer",
                        "store": "exploration_timer_s",
                        "type": "time",
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
                        "screen_id": "exploration",
                        "ocr": "references/exploration.png",
                        "regions": [
                            {
                                "name": "exploration_timer",
                                "action": "text",
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
async def test_ocr_step_time_type_stores_seconds_as_int(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """``type: time`` OCR step converts "01:02:03" → 3723 in Redis."""
    _write_timer_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _StubOcrClient:
        async def ocr_region(
            self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
        ) -> OCRResult:
            return OCRResult(region_id="r0", text="01:02:03", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-time",
        player_id="player_42",
        scenario_key="read_timer",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    # 1h 2m 3s = 3723 seconds.
    assert final["exploration_timer_s"] == "3723"
    # Raw OCR text is preserved alongside the coerced value.
    assert final["exploration_timer_s_text"] == "01:02:03"


@pytest.mark.asyncio
async def test_ocr_event_timer_stores_structured_sqlite_snapshot(
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
) -> None:
    db_path = tmp_path / "state.db"
    set_state_db_path_for_tests(db_path)
    monkeypatch.setattr(state_store_module, "_global_store", None)
    fixed_now = 1_700_000_000.0
    monkeypatch.setattr(dsl_ocr_mixin.time, "time", lambda: fixed_now)
    try:
        scenario_root = _scenario_root(tmp_path)
        (scenario_root / "shop").mkdir(parents=True)
        (scenario_root / "shop" / "event_timer.yaml").write_text(
            yaml.dump(
                {
                    "enabled": True,
                    "name": "Read event timer",
                    "device_level": True,
                    "steps": [
                        {
                            "ocr": "artisans_trove.delay",
                            "event_timer": "shop.artisans_trove",
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
                            "screen_id": "shop",
                            "ocr": "references/shop.png",
                            "regions": [
                                {
                                    "name": "artisans_trove.delay",
                                    "action": "text",
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
        actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

        class _StubOcrClient:
            async def ocr_region(
                self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
            ) -> OCRResult:
                return OCRResult(
                    region_id="artisans_trove.delay",
                    text="1d 09:11:19",
                    confidence=0.97,
                )

        import ocr.client as ocr_client_module

        mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
        patch_dsl(mocker, actions, repo_root=tmp_path)

        task = dsl.DslScenarioTask(
            task_id="t-event-timer",
            player_id="42",
            scenario_key="event_timer",
            redis_client=redis_async,  # type: ignore[arg-type]
        )
        result = await task.execute("bs1")

        assert result.success is True
        redis_state = await redis_async.hgetall("wos:player:42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert redis_state["artisans_trove.delay"] == "1d 09:11:19"

        store = get_state_store().get("42")
        assert store is not None
        timer = store.snapshot().event_timers["shop.artisans_trove"]
        assert timer.remaining_s == 119479
        assert timer.recorded_at == fixed_now
        assert timer.reset_at == fixed_now + 119479
        assert timer.raw_text == "1d 09:11:19"
        assert timer.source_region == "artisans_trove.delay"
        assert timer.confidence == 0.97
    finally:
        set_state_db_path_for_tests(None)
        state_store_module._global_store = None


@pytest.mark.asyncio
async def test_ocr_step_time_with_throttle_push_writes_push_ttl(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """``type: time`` + ``throttle_push: <scenario>`` writes the push_ttl
    marker with TTL = parsed seconds. Future overlay pushes of the named
    scenario are dropped until the marker expires.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "building").mkdir(parents=True)
    (scenario_root / "building" / "throttle.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Throttle building",
                "device_level": True,
                "steps": [
                    {
                        "ocr": "building.upgrading.time",
                        "type": "time",
                        "throttle_push": "building.upgrade",
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
                        "screen_id": "building",
                        "ocr": "references/building.upgrading.png",
                        "regions": [
                            {
                                "name": "building.upgrading.time",
                                "action": "text",
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
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _StubOcrClient:
        async def ocr_region(
            self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
        ) -> OCRResult:
            return OCRResult(region_id="r0", text="01:00:00", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    # Seed active_player on the instance so throttle_push uses player-scoped key,
    # matching ``_enqueue_push_scenarios_from_overlay``'s scope resolution.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "active_player", "player_77"
    )

    task = dsl.DslScenarioTask(
        task_id="t-throttle",
        player_id="",
        scenario_key="throttle",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    # Player-scoped throttle key (active_player resolved from instance state).
    throttle_val = await redis_async.get(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_77:push_ttl:building.upgrade"
    )
    assert throttle_val == "1"
    # TTL is roughly 3600s (1h) — within a small slack window.
    ttl = await redis_async.ttl(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_77:push_ttl:building.upgrade"
    )
    assert 3590 <= int(ttl) <= 3600


@pytest.mark.asyncio
async def test_ocr_step_time_throttle_push_no_active_player_uses_instance_scope(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """When no ``active_player`` is set anywhere, throttle key falls back to
    per-instance scope — mirrors ``_enqueue_push_scenarios_from_overlay``.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "building").mkdir(parents=True)
    (scenario_root / "building" / "throttle.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Throttle building",
                "device_level": True,
                "steps": [
                    {
                        "ocr": "building.upgrading.time",
                        "type": "time",
                        "throttle_push": "building.upgrade",
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
                        "screen_id": "building",
                        "ocr": "references/building.upgrading.png",
                        "regions": [
                            {
                                "name": "building.upgrading.time",
                                "action": "text",
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
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _StubOcrClient:
        async def ocr_region(
            self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
        ) -> OCRResult:
            return OCRResult(region_id="r0", text="00:05:00", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-throttle-noap",
        player_id="",
        scenario_key="throttle",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    throttle_val = await redis_async.get(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:push_ttl:building.upgrade"
    )
    assert throttle_val == "1"
    ttl = await redis_async.ttl(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:push_ttl:building.upgrade"
    )
    assert 290 <= int(ttl) <= 300


@pytest.mark.asyncio
async def test_ocr_step_time_building_upgrading_reference_image(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Lock in the production bbox + parse round-trip for the real WOS
    ``building.upgrading.png`` screenshot:

    1. The bbox→px crop math (``_persist_ocr_result``) hits the correct
       region (``(520, 710, 111, 35)`` on a 720×1280 frame).
    2. The OCR client's response ("00:00:27" — captured live against the
       reference image with local OCR) is parsed as 27 seconds.
    3. The throttle marker for ``building.upgrade`` lands in Redis with the
       parsed-seconds TTL.

    Production bbox values come from ``area.json`` region
    ``building.upgrading.time`` (id 61). If the area is re-labeled in the
    annotator and the px crop shifts, this test fails — a deliberate guard
    against silent breakage of the chapter-task throttle path.
    """
    img = cv2.imread(
        str(
            Path(
                "games/wos/core/building/common/references/building.upgrading.png"
            ).resolve()
        )
    )
    assert img is not None, "reference image is missing"
    h, w = img.shape[:2]
    assert (w, h) == (720, 1280), "reference image must be the 720×1280 capture baseline"

    bbox = {
        "x": 72.16216216216216,
        "y": 55.43478260869565,
        "width": 15.3996138996139,
        "height": 2.7,
    }

    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "building").mkdir(parents=True)
    (scenario_root / "building" / "building_upgrade.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Building upgrade reference",
                "device_level": True,
                "steps": [
                    {
                        "ocr": "building.upgrading.time",
                        "type": "time",
                        "throttle_push": "building.upgrade",
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
                        "id": 61,
                        "screen_id": "",
                        "ocr": "references/building.upgrading.png",
                        "regions": [
                            {
                                "name": "building.upgrading.time",
                                "action": "text",
                                "type": "time",
                                "threshold": 0.5,
                                "bbox": bbox,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    actions = make_actions(img, resolution=(w, h))
    captured: dict[str, Any] = {}

    class _StubOcrClient:
        async def ocr_region(
            self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
        ) -> OCRResult:
            # Capture the cropped region so the test asserts the bbox→px
            # math instead of trusting it. Numbers come from
            # ``int(round(pct/100 * dim))`` in ``_persist_ocr_result``.
            captured["region"] = region
            captured["image_shape"] = image.shape
            return OCRResult(
                region_id="building.upgrading.time",
                text="00:00:27",
                confidence=0.9995,
            )

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-bu-real",
        player_id="765502864",
        scenario_key="building_upgrade",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    # bbox→px crop on 720×1280: x=520, y=710, w=111, h=35.
    assert captured["region"] == LayoutRegion(520, 710, 111, 35)
    assert captured["image_shape"] == (1280, 720, 3)

    # Throttle marker: scope picks the live player_id (task carries it).
    throttle_val = await redis_async.get(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:765502864:push_ttl:building.upgrade"
    )
    assert throttle_val == "1"
    ttl = await redis_async.ttl(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:765502864:push_ttl:building.upgrade"
    )
    # 27 seconds is small; allow ±2 for redis rounding / test scheduling.
    assert 25 <= int(ttl) <= 27


@pytest.mark.asyncio
async def test_ocr_step_time_unparseable_skips_persist(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """OCR text that can't be parsed as HH:MM:SS / MM:SS is logged + skipped,
    not persisted as the raw garbage string (which would poison downstream
    arithmetic ``cond`` checks).
    """
    _write_timer_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _StubOcrClient:
        async def ocr_region(
            self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any
        ) -> OCRResult:
            return OCRResult(region_id="r0", text="??:??:??", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-time-bad",
        player_id="player_42",
        scenario_key="read_timer",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert "exploration_timer_s" not in final
