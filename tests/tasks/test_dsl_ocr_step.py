from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from century.api import CenturyAPIError, PlayerData
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from layout.area_manifest import load_area_doc
from layout.types import Region as LayoutRegion
from ocr.client import OcrClient, OCRResult
from ocr.preprocess import resolve_preprocess


def _scenario_root(tmp_path: Path) -> Path:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


def _write_who_i_am_repo(
    tmp_path: Path,
    *,
    player_id_min_digits: int | None = None,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    player_id_step: dict[str, Any] = {
        "ocr": "player.id",
        "store": "player_id",
        "type": "integer",
    }
    if player_id_min_digits is not None:
        player_id_step["min_digits"] = player_id_min_digits
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "who_i_am.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Who am I",
                # Matches production: this scenario bootstraps player identity
                # itself, so it must run before any ``player_id`` is known and
                # is exempt from the implicit player-identity gate.
                "device_level": True,
                "steps": [player_id_step],
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
async def test_ocr_step_persists_integer_to_player_state(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async

    captured: dict[str, Any] = {}

    # Matches the in-game ``chief_profile`` reference (the OCR'd numeric ID
    # printed under the chief avatar). The wrapper text + spacing imitates how
    # the game renders the line so the digit-extraction path is exercised end
    # to end (``re.sub(r"\D+", "", ...)``).
    REAL_OCR_TEXT = "ID: 765 502 864"
    EXPECTED_PLAYER_ID = "765502864"

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            captured["region"] = region
            captured["image_shape"] = image.shape
            return OCRResult(region_id="r0", text=REAL_OCR_TEXT, confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr",
        player_id="player_42",
        scenario_key="who_i_am",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    # bbox is resolved against GAME_FRAME_SIZE (720×1280), not the captured frame.
    # x=25%, y=50%, w=50%, h=10% → (180, 640, 360, 128).
    assert captured["region"] == LayoutRegion(180, 640, 360, 128)
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert final["player_id"] == EXPECTED_PLAYER_ID
    assert final["player_id_text"] == REAL_OCR_TEXT
    assert float(final["player_id_confidence"]) == pytest.approx(0.97, abs=1e-3)
    assert "player_id_at" in final


@pytest.mark.asyncio
async def test_device_level_who_i_am_promotes_ocr_player_id_to_active_player(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="ID: 765 502 864", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-device",
        player_id="",
        scenario_key="who_i_am",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert task.player_id == "765502864"
    p = await redis_async.hget("wos:player:765502864:state", "player_id")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert p == "765502864"
    ap = await redis_async.hget("wos:instance:bs1:state", "active_player")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert ap == "765502864"


@pytest.mark.asyncio
async def test_device_level_who_i_am_attaches_running_package_to_account(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Resolving identity pins the account to the build it ran on so gift codes
    can later skip beta-alias accounts (``config.devices.set_gamer_package``)."""
    _write_who_i_am_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))
    # The controller stamps the running package onto instance state per tick.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", mapping={"last_game_package": "com.xyz.gof"}
    )

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="ID: 765 502 864", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    import config.devices as devices_module

    set_pkg = mocker.patch.object(devices_module, "set_gamer_package", return_value=True)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-pkg",
        player_id="",
        scenario_key="who_i_am",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    set_pkg.assert_called_once_with("765502864", "com.xyz.gof")


@pytest.mark.asyncio
async def test_ocr_step_skips_persist_below_threshold(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async

    class _LowConfStub:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="42", confidence=0.10)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _LowConfStub)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-low",
        player_id="player_42",
        scenario_key="who_i_am",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    v = await redis_async.hget("wos:player:player_42:state", "player_id")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert v in {None, ""}, "low-confidence OCR must not persist player_id"


@pytest.mark.asyncio
async def test_device_level_who_i_am_retries_when_player_id_digits_too_short(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path, player_id_min_digits=8)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _ShortIdStub:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="2721690 &", confidence=0.97)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _ShortIdStub)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-short-device",
        player_id="",
        scenario_key="who_i_am",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "identity_not_resolved"
    ap = await redis_async.hget("wos:instance:bs1:state", "active_player")  # type: ignore[attr-defined]
    assert ap in {None, ""}
    last = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]
    assert last["dsl_last_ocr_status"] == "integer_too_short"
    assert last["dsl_last_ocr_value"] == "2721690"


@pytest.mark.asyncio
async def test_device_level_who_i_am_retries_when_identity_not_resolved(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _LowConfStub:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="42", confidence=0.10)

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _LowConfStub)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-low-device",
        player_id="",
        scenario_key="who_i_am",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "identity_not_resolved"
    assert result.next_run_at is not None


@pytest.mark.asyncio
async def test_consecutive_ocr_steps_share_one_capture_and_request(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "read_two.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "steps": [
                    {"ocr": "player_id", "store": "player_id", "type": "integer"},
                    {"ocr": "chapter.task", "store": "chapter_task", "scope": "instance"},
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
                        "screen_id": "main",
                        "ocr": "references/main.png",
                        "regions": [
                            {
                                "name": "player_id",
                                "action": "text",
                                "type": "integer",
                                "digit_count": 9,
                                "digit_x0": 4,
                                "bbox": {"x": 10, "y": 10, "width": 20, "height": 10},
                            },
                            {
                                "name": "chapter.task",
                                "action": "text",
                                "bbox": {"x": 40, "y": 50, "width": 30, "height": 20},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async
    captured: dict[str, Any] = {"calls": 0}

    class _BulkOcrClient:
        async def ocr_regions(
            self, image: np.ndarray, regions: list[LayoutRegion], **kwargs: Any
        ) -> list[OCRResult]:
            captured["calls"] += 1
            captured["image_shape"] = image.shape
            captured["regions"] = regions
            captured["region_preprocess"] = kwargs.get("region_preprocess")
            captured["region_digit_count"] = kwargs.get("region_digit_count")
            captured["region_digit_x0"] = kwargs.get("region_digit_x0")
            return [
                OCRResult(region_id="r0", text="ID: 765 502 864", confidence=0.99),
                OCRResult(region_id="r1", text="Upgrade Furnace to Lv. 8", confidence=0.88),
            ]

    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _BulkOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-bulk",
        player_id="player_42",
        scenario_key="read_two",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert (
        actions.capture_screen_bgr.call_count
        + actions.capture_screen_bgr_cached.call_count
    ) == 1
    assert captured["calls"] == 1
    # Regions are resolved against GAME_FRAME_SIZE (720×1280), so
    # 10%×720=72, 10%×1280=128, 20%×720=144, 10%×1280=128 and
    # 40%×720=288, 50%×1280=640, 30%×720=216, 20%×1280=256.
    assert captured["regions"] == [
        LayoutRegion(72, 128, 144, 128),
        LayoutRegion(288, 640, 216, 256),
    ]
    assert captured["region_preprocess"] == ["fast_digits", None]
    assert captured["region_digit_count"] == [9, None]
    assert captured["region_digit_x0"] == [4, 0]
    pid = await redis_async.hget("wos:player:player_42:state", "player_id")  # type: ignore[attr-defined]
    assert pid == "765502864"
    task_txt = await redis_async.hget("wos:instance:bs1:state", "chapter_task")  # type: ignore[attr-defined]
    assert task_txt == "Upgrade Furnace to Lv. 8"


@pytest.mark.asyncio
async def test_exec_sync_building_name_persists_detected_level(
    mocker,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    import tasks.dsl_exec as dsl_exec
    from config.buildings import BuildingDef, BuildingRegistry
    from tasks.dsl_exec import sync_state

    captured: dict[str, Any] = {}

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            captured["player_id"] = player_id
            captured["nickname"] = nickname
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = flat

    mocker.patch.object(
        sync_state,
        "get_building_registry",
        side_effect=lambda: BuildingRegistry(buildings=(BuildingDef(id="cookhouse", name="Cookhouse"),)),
    )
    mocker.patch.object(sync_state, "get_state_store", side_effect=lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_42:state",
        mapping={"building.name": "Cookhouse Lv. 1"},
    )

    with caplog.at_level(logging.INFO):
        await dsl_exec.DSL_EXEC_REGISTRY["sync_building_name"](
            dsl_exec.DslExecContext(
                redis_client=redis_async,
                player_id="player_42",
                instance_id="bs1",
            )
        )

    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert final["buildings.levels.cookhouse"] == "1"
    assert final["building.name.parsed_id"] == "cookhouse"
    assert final["building.name.parsed_name"] == "Cookhouse"
    assert final["building.name.parsed_level"] == "1"
    assert captured["player_id"] == "player_42"
    assert captured["flat"] == {
        "buildings.levels.cookhouse": 1,
        "buildings.state.text": "Cookhouse Lv. 1",
    }
    assert "dsl exec sync_building_name: updated" in caplog.text
    assert "building=cookhouse" in caplog.text
    assert "old=?" in caplog.text
    assert "new=1" in caplog.text
    assert "source=player" in caplog.text


@pytest.mark.asyncio
async def test_exec_sync_building_name_logs_unchanged_level(
    mocker,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    import tasks.dsl_exec as dsl_exec
    from config.buildings import BuildingDef, BuildingRegistry
    from tasks.dsl_exec import sync_state

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            pass

    mocker.patch.object(
        sync_state,
        "get_building_registry",
        side_effect=lambda: BuildingRegistry(buildings=(BuildingDef(id="coal_mine", name="Coal Mine"),)),
    )
    mocker.patch.object(sync_state, "get_state_store", side_effect=lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_42:state",
        mapping={
            "building.name": "Coal Mine Lv.1",
            "buildings.levels.coal_mine": "1",
        },
    )

    with caplog.at_level(logging.INFO):
        await dsl_exec.DSL_EXEC_REGISTRY["sync_building_name"](
            dsl_exec.DslExecContext(
                redis_client=redis_async,
                player_id="player_42",
                instance_id="bs1",
            )
        )

    assert "dsl exec sync_building_name: unchanged" in caplog.text
    assert "building=coal_mine" in caplog.text
    assert "old=1" in caplog.text
    assert "new=1" in caplog.text


@pytest.mark.asyncio
async def test_exec_sync_building_name_uses_active_player_instance_fallback(
    mocker,
    redis_async: object,
) -> None:
    import tasks.dsl_exec as dsl_exec
    from config.buildings import BuildingDef, BuildingRegistry
    from tasks.dsl_exec import sync_state

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            pass

    mocker.patch.object(
        sync_state,
        "get_building_registry",
        side_effect=lambda: BuildingRegistry(buildings=(BuildingDef(id="lancer_camp", name="Lancer Camp"),)),
    )
    mocker.patch.object(sync_state, "get_state_store", side_effect=lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={
            "active_player": "player_42",
            "building.name": "Lancer Camp Lv. 12",
        },
    )

    await dsl_exec.DSL_EXEC_REGISTRY["sync_building_name"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="",
            instance_id="bs1",
        )
    )

    level = await redis_async.hget(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_42:state",
        "buildings.levels.lancer_camp",
    )
    assert level == "12"


@pytest.mark.asyncio
async def test_exec_sync_hero_unit_persists_name_and_level(
    mocker,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    """``sync_hero_unit`` reads OCR'd name+level from Redis and writes a
    typed snapshot to ``heroes.entries.<slug>`` in state.yaml."""
    import tasks.dsl_exec as dsl_exec
    from tasks.dsl_exec import sync_state

    captured: dict[str, Any] = {}

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            captured["player_id"] = player_id
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = flat

    mocker.patch.object(sync_state, "get_state_store", side_effect=lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_42:state",
        mapping={
            "page.heroes.unit.name": "Bahiti",
            "page.heroes.unit.level": "5",
        },
    )

    with caplog.at_level(logging.INFO):
        await dsl_exec.DSL_EXEC_REGISTRY["sync_hero_unit"](
            dsl_exec.DslExecContext(
                redis_client=redis_async,
                player_id="player_42",
                instance_id="bs1",
            )
        )

    assert captured["player_id"] == "player_42"
    flat = captured["flat"]
    assert "heroes.entries.bahiti" in flat
    snapshot = flat["heroes.entries.bahiti"]
    assert snapshot["name"] == "Bahiti"
    assert snapshot["level"] == 5
    assert isinstance(snapshot["seen_at"], float)
    assert "hero=bahiti" in caplog.text
    assert "level=5" in caplog.text


@pytest.mark.asyncio
async def test_exec_sync_hero_unit_skips_when_name_missing(
    mocker,
    redis_async: object,
) -> None:
    """No OCR'd name → no state.yaml write, no crash."""
    import tasks.dsl_exec as dsl_exec
    from tasks.dsl_exec import sync_state

    state_store_called = False

    class _FakeStore:
        def get_or_create(self, *_a: Any, **_kw: Any) -> Any:
            nonlocal state_store_called
            state_store_called = True
            return self

        def update_from_flat(self, *_a: Any, **_kw: Any) -> None:
            nonlocal state_store_called
            state_store_called = True

    mocker.patch.object(sync_state, "get_state_store", side_effect=lambda: _FakeStore())
    # Only level set; name is missing.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_42:state",
        mapping={"page.heroes.unit.level": "3"},
    )

    await dsl_exec.DSL_EXEC_REGISTRY["sync_hero_unit"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )
    assert state_store_called is False


@pytest.mark.asyncio
async def test_exec_sync_hero_unit_normalises_messy_name_to_slug(
    mocker,
    redis_async: object,
) -> None:
    """OCR noise like punctuation / casing collapses to a stable hero ID."""
    import tasks.dsl_exec as dsl_exec
    from tasks.dsl_exec import sync_state

    captured: dict[str, Any] = {}

    class _FakeStore:
        def get_or_create(self, *_a: Any, **_kw: Any) -> Any:
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = flat

    mocker.patch.object(sync_state, "get_state_store", side_effect=lambda: _FakeStore())
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:player_42:state",
        mapping={
            "page.heroes.unit.name": "Sgt. Black-eye!",
            "page.heroes.unit.level": "9",
        },
    )

    await dsl_exec.DSL_EXEC_REGISTRY["sync_hero_unit"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )

    flat = captured["flat"]
    assert "heroes.entries.sgtblackeye" in flat
    # Original name preserved inside the snapshot.
    assert flat["heroes.entries.sgtblackeye"]["name"] == "Sgt. Black-eye!"


@pytest.mark.asyncio
async def test_exec_fetch_player_syncs_century_fields(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "steps": [{"exec": "fetch_player"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    redis_client = redis_async
    await redis_async.hset("wos:player:player_42:state", mapping={"player_id": "765502864"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    captured: dict[str, Any] = {}

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        captured["fid"] = fid
        return PlayerData(
            fid=fid,
            nickname="TestNick",
            kid=55,
            stove_level=30,
            avatar_image="http://example/a.png",
            stove_lv_content=100,
        )

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    from century.api import CenturyClient

    mocker.patch.object(CenturyClient, "fetch_player", new=fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec",
        player_id="player_42",
        scenario_key="sync_century",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert captured.get("fid") == 765502864
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert final["nickname"] == "TestNick"
    assert final["stove_level"] == "30"
    assert final["kid"] == "55"
    assert final["stove_lv_content"] == "100"
    assert final["avatar_image"] == "http://example/a.png"
    assert "century_player_sync_at" in final


@pytest.mark.asyncio
async def test_exec_fetch_player_api_error_is_soft_failure(
    tmp_path: Path,
    mocker,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump({"enabled": True, "steps": [{"exec": "fetch_player"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    redis_client = redis_async
    await redis_async.hset("wos:player:player_42:state", mapping={"player_id": "765502864"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    calls = {"count": 0}

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        calls["count"] += 1
        msg = "player HTTP 403: Forbidden"
        raise CenturyAPIError(msg)

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    from century.api import CenturyClient

    mocker.patch.object(CenturyClient, "fetch_player", new=fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec-soft",
        player_id="player_42",
        scenario_key="sync_century",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    with caplog.at_level("WARNING"):
        result = await task.execute("bs1")

    assert result.success is True
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert final["player_id"] == "765502864"
    assert "nickname" not in final
    assert "century_player_sync_failed_at" in final
    assert final["century_player_sync_error"] == "player HTTP 403: Forbidden"
    assert "player HTTP 403" in caplog.text

    result2 = await task.execute("bs1")
    assert result2.success is True
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_exec_fetch_player_role_not_exist_clears_active_player(
    tmp_path: Path,
    mocker,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump({"enabled": True, "steps": [{"exec": "fetch_player"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    from config.devices_db import get_last_active_player, set_last_active_player, upsert_device

    upsert_device("bs2", adb_serial="127.0.0.1:5625")
    assert set_last_active_player("bs2", "2721690") is True

    redis_client = redis_async
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:2721690:state",
        mapping={"player_id": "2721690"},
    )
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs2:state",
        # Canonical (prod) build → a 40001 means the OCR'd id is genuinely
        # invalid, so clearing to re-trigger who_i_am is the intended self-heal.
        mapping={"active_player": "2721690", "last_game_is_beta": "0"},
    )
    calls = {"count": 0}

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        calls["count"] += 1
        msg = "player fetch failed: role not exist. err_code=40001"
        raise CenturyAPIError(
            msg,
            err_code=40001,
            api_msg="role not exist",
            endpoint="player",
        )

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    from century.api import CenturyClient

    mocker.patch.object(CenturyClient, "fetch_player", new=fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec-role-missing",
        player_id="2721690",
        scenario_key="sync_century",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    with caplog.at_level("WARNING"):
        result = await task.execute("bs2")

    assert result.success is True
    final = await redis_async.hgetall("wos:player:2721690:state")  # type: ignore[attr-defined]
    assert final["century_player_sync_err_code"] == "40001"
    assert final["century_player_sync_error"] == "role not exist"
    ap = await redis_async.hget("wos:instance:bs2:state", "active_player")  # type: ignore[attr-defined]
    assert ap in {None, ""}
    invalid = await redis_async.hget("wos:instance:bs2:state", "invalid_player_id")  # type: ignore[attr-defined]
    assert invalid == "2721690"
    assert get_last_active_player("bs2") == ""
    assert "role not exist" in caplog.text

    assert set_last_active_player("bs2", "2721690") is True
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs2:state",
        mapping={"active_player": "2721690"},
    )
    result2 = await task.execute("bs2")
    assert result2.success is True
    assert calls["count"] == 1
    ap2 = await redis_async.hget("wos:instance:bs2:state", "active_player")  # type: ignore[attr-defined]
    assert ap2 in {None, ""}
    assert get_last_active_player("bs2") == ""


@pytest.mark.asyncio
async def test_exec_fetch_player_skips_century_on_beta_build(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Beta build → Century is never called and the OCR-bound active_player
    survives. Century can't see beta accounts (40001), so the sync is pointless
    and would otherwise clear a perfectly valid identity."""
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump({"enabled": True, "steps": [{"exec": "fetch_player"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:2721690:state", mapping={"player_id": "2721690"}
    )
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs3:state",
        mapping={"active_player": "2721690", "last_game_is_beta": "1"},
    )
    calls = {"count": 0}

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        calls["count"] += 1
        msg = "should never be called on beta"
        raise AssertionError(msg)

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    from century.api import CenturyClient

    mocker.patch.object(CenturyClient, "fetch_player", new=fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec-beta",
        player_id="2721690",
        scenario_key="sync_century",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs3")

    assert result.success is True
    assert calls["count"] == 0
    ap = await redis_async.hget("wos:instance:bs3:state", "active_player")  # type: ignore[attr-defined]
    assert ap == "2721690"
    final = await redis_async.hgetall("wos:player:2721690:state")  # type: ignore[attr-defined]
    assert "century_player_sync_failed_at" not in final


@pytest.mark.asyncio
async def test_exec_fetch_player_role_not_exist_keeps_active_player_when_build_unknown(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """40001 on an *unknown* build (controller hasn't written last_game_is_beta
    yet) must NOT clear active_player — the account may be a beta alias whose
    OCR identity is valid. We record the failure but keep the binding."""
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump({"enabled": True, "steps": [{"exec": "fetch_player"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:2721690:state", mapping={"player_id": "2721690"}
    )
    # No last_game_is_beta field → build unknown.
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs4:state", mapping={"active_player": "2721690"}
    )

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        msg = "role not exist. err_code=40001"
        raise CenturyAPIError(
            msg,
            err_code=40001,
            api_msg="role not exist",
            endpoint="player",
        )

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    from century.api import CenturyClient

    mocker.patch.object(CenturyClient, "fetch_player", new=fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec-unknown",
        player_id="2721690",
        scenario_key="sync_century",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs4")

    assert result.success is True
    ap = await redis_async.hget("wos:instance:bs4:state", "active_player")  # type: ignore[attr-defined]
    assert ap == "2721690"  # binding preserved
    invalid = await redis_async.hget("wos:instance:bs4:state", "invalid_player_id")  # type: ignore[attr-defined]
    assert invalid in {None, ""}
    final = await redis_async.hgetall("wos:player:2721690:state")  # type: ignore[attr-defined]
    assert final["century_player_sync_err_code"] == "40001"


# ---------------------------------------------------------------------------
# Integration: real OCR backend against the labelled chief_profile reference.
# This test is intentionally NOT auto-skipped — missing reference image, missing
# area.json, or an unreachable OCR backend are all hard failures, because they
# break the production `who_i_am` flow. Bring up the OCR stack
# (Tesseract + eng.traineddata) before running the suite.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHIEF_PROFILE_REF = (
    _REPO_ROOT / "games/wos/core/chief_profile/references/chief_profile.png"
)
# Real in-game player_id printed on the labelled chief_profile reference image.
_REFERENCE_PLAYER_ID = "765502864"


def _assert_local_ocr_available() -> None:
    from config.loader import get_settings

    settings = get_settings()
    cmd = str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract")
    assert shutil.which(cmd), (
        f"Tesseract executable not found: {cmd!r}. "
        "Install Tesseract with eng.traineddata before running this test."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ocr_chief_profile_player_id_against_real_tesseract() -> None:
    """Real-OCR sanity check: the labelled `chief_profile.png` shows player_id 765502864.

    Hits local Tesseract OCR with the exact bbox declared in
    `area.json` for the `player_id` region, verifying both the integration wiring
    and that OCR remains accurate enough for `who_i_am` to identify the player.

    NOTE: this test does not skip — if local OCR is unavailable or the reference
    image / area.json is missing, the test fails. That is intentional: those
    are required for the production `who_i_am` flow to work.
    """
    import cv2  # local import — heavy module, not needed by the unit tests above

    from layout.area_lookup import screen_region_by_name
    from ocr.client import OcrClient

    assert _CHIEF_PROFILE_REF.is_file(), f"reference image missing: {_CHIEF_PROFILE_REF}"
    _assert_local_ocr_available()

    image = cv2.imread(str(_CHIEF_PROFILE_REF))
    assert image is not None, f"failed to decode {_CHIEF_PROFILE_REF}"
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = load_area_doc(_REPO_ROOT)
    # Canonical region name in area.json is ``player.id`` (dotted), which is
    # also what ``who_i_am`` scenario consumes via ``ocr: player.id``.
    pair = screen_region_by_name(area_doc, "player.id")
    assert pair is not None, "area.json has no `player.id` region"
    bbox = pair[1].get("bbox")
    assert isinstance(bbox, dict), f"`player.id` region is missing a bbox: {pair[1]!r}"

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    assert pw > 0 and ph > 0, f"degenerate pixel bbox: {(px, py, pw, ph)}"

    from config.loader import get_settings

    result = await OcrClient(get_settings()).ocr_region(image, LayoutRegion(px, py, pw, ph))
    digits = re.sub(r"\D+", "", result.text or "")
    assert digits == _REFERENCE_PLAYER_ID, (
        f"OCR did not match the labelled player_id on chief_profile.png. "
        f"expected={_REFERENCE_PLAYER_ID!r} text={result.text!r} digits={digits!r} "
        f"confidence={result.confidence:.4f} pixel_bbox=(x={px}, y={py}, w={pw}, h={ph})"
    )
    assert result.confidence >= 0.5, (
        f"OCR confidence too low for `player_id`: {result.confidence:.4f} "
        f"(text={result.text!r})"
    )


_CHIEF_PROFILE_LIVE_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "chief_profile_player_id_live.png"
_LIVE_PLAYER_ID = "401227964"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ocr_chief_profile_player_id_live_tesseract_fast_digits_pipeline(
    ocr_client: OcrClient,
) -> None:
    """Regression: ``who_i_am`` reads live ``player.id`` via the production pipeline.

    Resolves the preprocess from the area doc (``fast_digits`` — PSM 7 + digit
    whitelist) so the test tracks production instead of a hard-coded tag.

    Fixture copied from ``temporal/bs1_approval_current.png``.
    """
    import cv2

    from layout.area_lookup import screen_region_by_name

    assert _CHIEF_PROFILE_LIVE_FIXTURE.is_file(), (
        f"fixture missing: {_CHIEF_PROFILE_LIVE_FIXTURE}"
    )
    _assert_local_ocr_available()

    image = cv2.imread(str(_CHIEF_PROFILE_LIVE_FIXTURE))
    assert image is not None
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = load_area_doc(_REPO_ROOT)
    pair = screen_region_by_name(area_doc, "player.id")
    assert pair is not None
    bbox = pair[1].get("bbox")
    assert isinstance(bbox, dict)

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    region_px = LayoutRegion(px, py, pw, ph)

    area_threshold = float(pair[1].get("threshold") or 0.9)
    preprocess = resolve_preprocess(
        explicit=pair[1].get("preprocess"), type_hint=pair[1].get("type")
    )
    assert preprocess == "fast_digits", (
        f"player.id should resolve to fast_digits, got {preprocess!r}"
    )
    result = await ocr_client.ocr_region(
        image, region_px, preprocess=preprocess, digit_x0=0
    )

    ocr_digits = re.sub(r"\D+", "", result.text or "")
    assert ocr_digits == _LIVE_PLAYER_ID, (
        f"expected {_LIVE_PLAYER_ID!r}, got {ocr_digits!r} "
        f"text={result.text!r} conf={result.confidence:.4f}"
    )
    assert (result.text or "").strip() == _LIVE_PLAYER_ID
    assert result.confidence >= area_threshold, (
        f"fast_digits conf {result.confidence:.4f} below area.json threshold "
        f"{area_threshold:.3f} — who_i_am would skip store"
    )


@pytest.mark.asyncio
async def test_ocr_step_state_keyword_writes_to_state_yaml(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """``state: <path>`` writes the OCR value into ``db/state.yaml`` via state_store
    and *does not* touch Redis (since no ``store:`` was set)."""
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "by_cron").mkdir(parents=True)
    (scenario_root / "by_cron" / "check_squad.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Check squad",
                "steps": [
                    {
                        "ocr": "exploration.level",
                        "type": "integer",
                        "scope": "player",
                        "state": "exploration.level",
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
                        "id": 44,
                        "screen_id": "squad_settings",
                        "ocr": "references/squad_settings.png",
                        "regions": [
                            {
                                "name": "exploration.level",
                                "action": "text",
                                "type": "string",
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

    captured: dict[str, Any] = {"flat": None, "player_id": None}

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            captured["player_id"] = player_id
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = dict(flat)

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="Lv. 12", confidence=0.97)

    import config.state_store as state_store_module
    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(state_store_module, "get_state_store", side_effect=lambda: _FakeStore())

    task = dsl.DslScenarioTask(
        task_id="t-squad",
        player_id="player_42",
        scenario_key="check_squad",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert captured["player_id"] == "player_42"
    assert captured["flat"] == {"exploration.level": 12}

    # No ``store:`` → Redis player hash should be untouched.
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert "exploration.level" not in final
    assert "squad_settings.level" not in final


@pytest.mark.asyncio
async def test_ocr_step_state_and_store_together_write_both_targets(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Both keywords on one step → Redis (``store``) AND state.yaml (``state``)."""
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "by_cron").mkdir(parents=True)
    (scenario_root / "by_cron" / "check_squad.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Check squad",
                "steps": [
                    {
                        "ocr": "exploration.level",
                        "type": "integer",
                        "store": "level_redis",
                        "state": "exploration.level",
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
                        "id": 44,
                        "screen_id": "squad_settings",
                        "ocr": "references/squad_settings.png",
                        "regions": [
                            {
                                "name": "exploration.level",
                                "action": "text",
                                "type": "string",
                                "threshold": 0.5,
                                "bbox": {"x": 25.0, "y": 50.0, "width": 50.0, "height": 10.0},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    actions = make_actions(np.zeros((100, 200, 3), dtype=np.uint8))
    captured: dict[str, Any] = {"flat": None}

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = dict(flat)

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="Lv. 7", confidence=0.97)

    import config.state_store as state_store_module
    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(state_store_module, "get_state_store", side_effect=lambda: _FakeStore())

    task = dsl.DslScenarioTask(
        task_id="t-squad",
        player_id="player_42",
        scenario_key="check_squad",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert captured["flat"] == {"exploration.level": 7}
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert final["level_redis"] == "7"


@pytest.mark.asyncio
async def test_ocr_step_without_state_keyword_skips_state_store(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Without ``state:`` keyword the state_store is never touched (Redis-only path)."""
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "by_cron").mkdir(parents=True)
    (scenario_root / "by_cron" / "check_squad.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Check squad",
                "steps": [
                    {
                        "ocr": "squad_settings.level",
                        "type": "integer",
                        "scope": "player",
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
                        "id": 44,
                        "screen_id": "squad_settings",
                        "ocr": "references/squad_settings.png",
                        "regions": [
                            {
                                "name": "squad_settings.level",
                                "action": "text",
                                "type": "string",
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

    state_store_called = False

    class _FakeStore:
        def get_or_create(self, *_a: Any, **_kw: Any) -> Any:
            nonlocal state_store_called
            state_store_called = True
            return self

        def update_from_flat(self, *_a: Any, **_kw: Any) -> None:
            nonlocal state_store_called
            state_store_called = True

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="7", confidence=0.97)

    import config.state_store as state_store_module
    import ocr.client as ocr_client_module

    mocker.patch.object(ocr_client_module, "OcrClient", _StubOcrClient)
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(state_store_module, "get_state_store", side_effect=lambda: _FakeStore())

    task = dsl.DslScenarioTask(
        task_id="t-squad-nofs",
        player_id="player_42",
        scenario_key="check_squad",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert state_store_called is False
