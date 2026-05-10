from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl
from century.api import CenturyAPIError, PlayerData
from layout.types import Region as LayoutRegion
from ocr.client import OCRResult


class _FakeActions:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.tapped: list[tuple[str, int, int, str | None]] = []
        self.captures = 0

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 200, 100

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        self.captures += 1
        return self.frame

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _write_who_i_am_repo(tmp_path: Path) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "who_i_am.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Who am I",
                "steps": [
                    {"ocr": "player_id"},
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
                                "name": "player_id",
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
    monkeypatch: Any,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async

    captured: dict[str, Any] = {}

    # Matches the in-game ``chief_profile`` reference (the OCR'd numeric ID
    # printed under the chief avatar). The wrapper text + spacing imitates how
    # the game renders the line so the digit-extraction path is exercised end
    # to end (``re.sub(r"\D+", "", ...)``).
    REAL_OCR_TEXT = "ID: 765 502 864"
    EXPECTED_PLAYER_ID = "765502864"

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion) -> OCRResult:
            captured["region"] = region
            captured["image_shape"] = image.shape
            return OCRResult(region_id="r0", text=REAL_OCR_TEXT, confidence=0.97)

    import ocr.client as ocr_client_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _StubOcrClient)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t-ocr",
        player_id="player_42",
        scenario_key="who_i_am",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    # 200×100 frame, bbox x=25 y=50 w=50 h=10 (% of frame).
    assert captured["region"] == LayoutRegion(50, 50, 100, 10)
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]
    assert final["player_id"] == EXPECTED_PLAYER_ID
    assert final["player_id_text"] == REAL_OCR_TEXT
    assert float(final["player_id_confidence"]) == pytest.approx(0.97, abs=1e-3)
    assert "player_id_at" in final


@pytest.mark.asyncio
async def test_device_level_who_i_am_promotes_ocr_player_id_to_active_player(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion) -> OCRResult:
            return OCRResult(region_id="r0", text="ID: 765 502 864", confidence=0.97)

    import ocr.client as ocr_client_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _StubOcrClient)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-device",
        player_id="",
        scenario_key="who_i_am",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert task.player_id == "765502864"
    p = await redis_async.hget("wos:player:765502864:state", "player_id")  # type: ignore[attr-defined]
    assert p == "765502864"
    ap = await redis_async.hget("wos:instance:bs1:state", "active_player")  # type: ignore[attr-defined]
    assert ap == "765502864"


@pytest.mark.asyncio
async def test_ocr_step_skips_persist_below_threshold(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async

    class _LowConfStub:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion) -> OCRResult:
            return OCRResult(region_id="r0", text="42", confidence=0.10)

    import ocr.client as ocr_client_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _LowConfStub)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-low",
        player_id="player_42",
        scenario_key="who_i_am",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    v = await redis_async.hget("wos:player:player_42:state", "player_id")  # type: ignore[attr-defined]
    assert v in {None, ""}, "low-confidence OCR must not persist player_id"


@pytest.mark.asyncio
async def test_device_level_who_i_am_retries_when_identity_not_resolved(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))

    class _LowConfStub:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion) -> OCRResult:
            return OCRResult(region_id="r0", text="42", confidence=0.10)

    import ocr.client as ocr_client_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _LowConfStub)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

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
    monkeypatch: Any,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "read_two.yaml").write_text(
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
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = redis_async
    captured: dict[str, Any] = {"calls": 0}

    class _BulkOcrClient:
        async def ocr_regions(
            self, image: np.ndarray, regions: list[LayoutRegion]
        ) -> list[OCRResult]:
            captured["calls"] += 1
            captured["image_shape"] = image.shape
            captured["regions"] = regions
            return [
                OCRResult(region_id="r0", text="ID: 765 502 864", confidence=0.99),
                OCRResult(region_id="r1", text="Upgrade Furnace to Lv. 8", confidence=0.88),
            ]

    import ocr.client as ocr_client_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _BulkOcrClient)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t-ocr-bulk",
        player_id="player_42",
        scenario_key="read_two",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.captures == 1
    assert captured["calls"] == 1
    assert captured["regions"] == [
        LayoutRegion(20, 10, 40, 10),
        LayoutRegion(80, 50, 60, 20),
    ]
    pid = await redis_async.hget("wos:player:player_42:state", "player_id")  # type: ignore[attr-defined]
    assert pid == "765502864"
    task_txt = await redis_async.hget("wos:instance:bs1:state", "chapter_task")  # type: ignore[attr-defined]
    assert task_txt == "Upgrade Furnace to Lv. 8"


@pytest.mark.asyncio
async def test_exec_sync_building_name_persists_detected_level(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    import tasks.dsl_exec as dsl_exec
    from config.buildings import BuildingDef, BuildingRegistry

    captured: dict[str, Any] = {}

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            captured["player_id"] = player_id
            captured["nickname"] = nickname
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = flat

    monkeypatch.setattr(
        dsl_exec,
        "get_building_registry",
        lambda: BuildingRegistry(buildings=(BuildingDef(id="cookhouse", name="Cookhouse"),)),
    )
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]
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

    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]
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
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    import tasks.dsl_exec as dsl_exec
    from config.buildings import BuildingDef, BuildingRegistry

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            pass

    monkeypatch.setattr(
        dsl_exec,
        "get_building_registry",
        lambda: BuildingRegistry(buildings=(BuildingDef(id="coal_mine", name="Coal Mine"),)),
    )
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]
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
    monkeypatch: Any,
    redis_async: object,
) -> None:
    import tasks.dsl_exec as dsl_exec
    from config.buildings import BuildingDef, BuildingRegistry

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            pass

    monkeypatch.setattr(
        dsl_exec,
        "get_building_registry",
        lambda: BuildingRegistry(buildings=(BuildingDef(id="lancer_camp", name="Lancer Camp"),)),
    )
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: _FakeStore())

    await redis_async.hset(  # type: ignore[attr-defined]
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

    level = await redis_async.hget(  # type: ignore[attr-defined]
        "wos:player:player_42:state",
        "buildings.levels.lancer_camp",
    )
    assert level == "12"


@pytest.mark.asyncio
async def test_exec_fetch_player_syncs_century_fields(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "sync_century.yaml").write_text(
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
    await redis_async.hset("wos:player:player_42:state", mapping={"player_id": "765502864"})  # type: ignore[attr-defined]
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

    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dsl,
        "BotActions",
        lambda: _FakeActions(np.zeros((10, 10, 3), dtype=np.uint8)),
    )

    from century.api import CenturyClient

    monkeypatch.setattr(CenturyClient, "fetch_player", fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec",
        player_id="player_42",
        scenario_key="sync_century",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert captured.get("fid") == 765502864
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]
    assert final["nickname"] == "TestNick"
    assert final["stove_level"] == "30"
    assert final["kid"] == "55"
    assert final["stove_lv_content"] == "100"
    assert final["avatar_image"] == "http://example/a.png"
    assert "century_player_sync_at" in final


@pytest.mark.asyncio
async def test_exec_fetch_player_api_error_is_soft_failure(
    tmp_path: Path,
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump({"enabled": True, "steps": [{"exec": "fetch_player"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    redis_client = redis_async
    await redis_async.hset("wos:player:player_42:state", mapping={"player_id": "765502864"})  # type: ignore[attr-defined]

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        raise CenturyAPIError("player HTTP 403: Forbidden")

    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dsl,
        "BotActions",
        lambda: _FakeActions(np.zeros((10, 10, 3), dtype=np.uint8)),
    )

    from century.api import CenturyClient

    monkeypatch.setattr(CenturyClient, "fetch_player", fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec-soft",
        player_id="player_42",
        scenario_key="sync_century",
        redis_client=redis_client,  # type: ignore[arg-type]
    )
    with caplog.at_level("WARNING"):
        result = await task.execute("bs1")

    assert result.success is True
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]
    assert final["player_id"] == "765502864"
    assert "nickname" not in final
    assert "player HTTP 403" in caplog.text


# ---------------------------------------------------------------------------
# Integration: real OCR backend against the labelled chief_profile reference.
# This test is intentionally NOT auto-skipped — missing reference image, missing
# area.json, or an unreachable OCR backend are all hard failures, because they
# break the production `who_i_am` flow. Bring up the OCR stack
# (`docker-compose up ocr`) before running the suite.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CHIEF_PROFILE_REF = _REPO_ROOT / "references" / "chief_profile.png"
_AREA_JSON = _REPO_ROOT / "area.json"
# Real in-game player_id printed on the labelled chief_profile reference image.
_REFERENCE_PLAYER_ID = "765502864"


def _assert_ocr_service_reachable() -> str:
    from config.loader import get_settings

    settings = get_settings()
    base_url = str(getattr(settings.ocr, "url", "")).rstrip("/")
    assert base_url, "OCR service URL is not configured (settings.ocr.url)"
    try:
        with httpx.Client(timeout=2.0) as c:
            resp = c.get(f"{base_url}/health")
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - keep the original error in the failure message
        raise AssertionError(
            f"OCR service not reachable at {base_url}: {type(exc).__name__}: {exc}. "
            "Bring it up (e.g. `docker-compose up ocr`) before running this test."
        ) from exc
    return base_url


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ocr_chief_profile_player_id_against_real_service() -> None:
    """Real-OCR sanity check: the labelled `chief_profile.png` shows player_id 765502864.

    Hits the configured OCR backend (PaddleOCR) with the exact bbox declared in
    `area.json` for the `player_id` region, verifying both the integration wiring
    and that OCR remains accurate enough for `who_i_am` to identify the player.

    NOTE: this test does not skip — if the OCR service is down or the reference
    image / area.json are missing, the test fails. That is intentional: those
    are required for the production `who_i_am` flow to work.
    """
    import cv2  # local import — heavy module, not needed by the unit tests above

    from layout.area_lookup import screen_region_by_name
    from ocr.client import OcrClient

    assert _CHIEF_PROFILE_REF.is_file(), f"reference image missing: {_CHIEF_PROFILE_REF}"
    assert _AREA_JSON.is_file(), f"area.json missing: {_AREA_JSON}"
    _assert_ocr_service_reachable()

    image = cv2.imread(str(_CHIEF_PROFILE_REF))
    assert image is not None, f"failed to decode {_CHIEF_PROFILE_REF}"
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    pair = screen_region_by_name(area_doc, "player_id")
    assert pair is not None, "area.json has no `player_id` region"
    bbox = pair[1].get("bbox")
    assert isinstance(bbox, dict), f"`player_id` region is missing a bbox: {pair[1]!r}"

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    assert pw > 0 and ph > 0, f"degenerate pixel bbox: {(px, py, pw, ph)}"

    result = await OcrClient().ocr_region(image, LayoutRegion(px, py, pw, ph))
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


@pytest.mark.asyncio
async def test_ocr_step_to_state_true_syncs_to_state_store(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """``to_state: true`` mirrors the parsed OCR value into ``db/state.yaml`` via state_store."""
    (tmp_path / "scenarios" / "by_cron").mkdir(parents=True)
    (tmp_path / "scenarios" / "by_cron" / "check_squad.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Check squad",
                "steps": [
                    {
                        "ocr": "squad_settings.level",
                        "type": "integer",
                        "scope": "player",
                        "to_state": True,
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

    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))

    captured: dict[str, Any] = {"flat": None, "player_id": None}

    class _FakeStore:
        def get_or_create(self, player_id: str, nickname: str = "") -> Any:
            captured["player_id"] = player_id
            return self

        def update_from_flat(self, flat: dict[str, Any]) -> None:
            captured["flat"] = dict(flat)

    class _StubOcrClient:
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion) -> OCRResult:
            return OCRResult(region_id="r0", text="Lv. 12", confidence=0.97)

    import ocr.client as ocr_client_module
    import config.state_store as state_store_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _StubOcrClient)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)
    monkeypatch.setattr(state_store_module, "get_state_store", lambda: _FakeStore())

    task = dsl.DslScenarioTask(
        task_id="t-squad",
        player_id="player_42",
        scenario_key="check_squad",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert captured["player_id"] == "player_42"
    assert captured["flat"] == {"squad_settings.level": 12}

    # Redis hash still holds the string; state_store path is the typed sync.
    final = await redis_async.hgetall("wos:player:player_42:state")  # type: ignore[attr-defined]
    assert final["squad_settings.level"] == "12"


@pytest.mark.asyncio
async def test_ocr_step_to_state_false_skips_state_store(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """Without ``to_state: true`` the state_store is never touched (Redis-only path)."""
    (tmp_path / "scenarios" / "by_cron").mkdir(parents=True)
    (tmp_path / "scenarios" / "by_cron" / "check_squad.yaml").write_text(
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

    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))

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
        async def ocr_region(self, image: np.ndarray, region: LayoutRegion) -> OCRResult:
            return OCRResult(region_id="r0", text="7", confidence=0.97)

    import ocr.client as ocr_client_module
    import config.state_store as state_store_module

    monkeypatch.setattr(ocr_client_module, "OcrClient", _StubOcrClient)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)
    monkeypatch.setattr(state_store_module, "get_state_store", lambda: _FakeStore())

    task = dsl.DslScenarioTask(
        task_id="t-squad-nofs",
        player_id="player_42",
        scenario_key="check_squad",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert state_store_called is False
