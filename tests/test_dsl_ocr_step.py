from __future__ import annotations

import json
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


class _FakeRedis:
    def __init__(self) -> None:
        self.hsets: list[tuple[str, dict[str, str]]] = []

    async def hset(self, key: str, *args: Any, **kwargs: Any) -> None:
        mapping = kwargs.get("mapping")
        if mapping is None and args:
            first = args[0]
            if isinstance(first, dict):
                mapping = first
            elif len(args) >= 2:
                mapping = {str(args[0]): str(args[1])}
        if mapping is None:
            mapping = {}
        self.hsets.append((key, dict(mapping)))

    async def hget(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeRedisKV(_FakeRedis):
    """Redis fake with in-memory hashes for ``hget`` / merge ``hset``."""

    def __init__(self, initial: dict[str, dict[str, str]] | None = None) -> None:
        super().__init__()
        self.hashes: dict[str, dict[str, str]] = dict(initial) if initial else {}

    async def hset(self, key: str, *args: Any, **kwargs: Any) -> None:
        await super().hset(key, *args, **kwargs)
        mapping = kwargs.get("mapping")
        if mapping is None and args:
            first = args[0]
            if isinstance(first, dict):
                mapping = first
            elif len(args) >= 2:
                mapping = {str(args[0]): str(args[1])}
        if mapping:
            base = self.hashes.setdefault(key, {})
            for k, v in dict(mapping).items():
                ks = k.decode() if isinstance(k, bytes) else str(k)
                vs = v.decode() if isinstance(v, bytes) else str(v)
                base[ks] = vs

    async def hget(self, key: str, field: str) -> bytes | None:
        row = self.hashes.get(key)
        if not row:
            return None
        fs = field.decode() if isinstance(field, bytes) else str(field)
        v = row.get(fs)
        if v is None:
            return None
        return str(v).encode("utf-8")


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
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = _FakeRedis()

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
        redis_client=redis_client,
    )
    result = await task.execute("bs1")

    assert result.success is True
    # 200×100 frame, bbox x=25 y=50 w=50 h=10 (% of frame).
    assert captured["region"] == LayoutRegion(50, 50, 100, 10)
    persisted = [m for k, m in redis_client.hsets if k == "wos:player:player_42:state"]
    assert persisted, f"no player-state writes; observed={redis_client.hsets!r}"
    final = persisted[-1]
    assert final["player_id"] == EXPECTED_PLAYER_ID
    assert final["player_id_text"] == REAL_OCR_TEXT
    assert float(final["player_id_confidence"]) == pytest.approx(0.97, abs=1e-3)
    assert "player_id_at" in final


@pytest.mark.asyncio
async def test_device_level_who_i_am_promotes_ocr_player_id_to_active_player(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = _FakeRedisKV()

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
        redis_client=redis_client,
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert task.player_id == "765502864"
    assert redis_client.hashes["wos:player:765502864:state"]["player_id"] == "765502864"
    assert redis_client.hashes["wos:instance:bs1:state"]["active_player"] == "765502864"


@pytest.mark.asyncio
async def test_ocr_step_skips_persist_below_threshold(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_who_i_am_repo(tmp_path)
    actions = _FakeActions(np.zeros((100, 200, 3), dtype=np.uint8))
    redis_client = _FakeRedis()

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
        redis_client=redis_client,
    )
    result = await task.execute("bs1")

    assert result.success is True
    player_writes = [m for k, m in redis_client.hsets if k == "wos:player:player_42:state"]
    assert player_writes == [], "low-confidence OCR must not persist player_id"


@pytest.mark.asyncio
async def test_consecutive_ocr_steps_share_one_capture_and_request(
    tmp_path: Path,
    monkeypatch: Any,
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
    redis_client = _FakeRedisKV()
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
        redis_client=redis_client,
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.captures == 1
    assert captured["calls"] == 1
    assert captured["regions"] == [
        LayoutRegion(20, 10, 40, 10),
        LayoutRegion(80, 50, 60, 20),
    ]
    player_state = redis_client.hashes["wos:player:player_42:state"]
    assert player_state["player_id"] == "765502864"
    assert (
        redis_client.hashes["wos:instance:bs1:state"]["chapter_task"]
        == "Upgrade Furnace to Lv. 8"
    )


@pytest.mark.asyncio
async def test_exec_fetch_player_syncs_century_fields(
    tmp_path: Path,
    monkeypatch: Any,
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

    redis_client = _FakeRedisKV(
        {"wos:player:player_42:state": {"player_id": "765502864"}},
    )
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
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions(np.zeros((10, 10, 3), dtype=np.uint8)))

    from century.api import CenturyClient

    monkeypatch.setattr(CenturyClient, "fetch_player", fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec",
        player_id="player_42",
        scenario_key="sync_century",
        redis_client=redis_client,
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert captured.get("fid") == 765502864
    final = redis_client.hashes["wos:player:player_42:state"]
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
) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "sync_century.yaml").write_text(
        yaml.dump({"enabled": True, "steps": [{"exec": "fetch_player"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")

    redis_client = _FakeRedisKV(
        {"wos:player:player_42:state": {"player_id": "765502864"}},
    )

    async def fake_fetch_player(_self: Any, fid: int) -> PlayerData:
        raise CenturyAPIError("player HTTP 403: Forbidden")

    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions(np.zeros((10, 10, 3), dtype=np.uint8)))

    from century.api import CenturyClient

    monkeypatch.setattr(CenturyClient, "fetch_player", fake_fetch_player)

    task = dsl.DslScenarioTask(
        task_id="t-exec-soft",
        player_id="player_42",
        scenario_key="sync_century",
        redis_client=redis_client,
    )
    with caplog.at_level("WARNING"):
        result = await task.execute("bs1")

    assert result.success is True
    final = redis_client.hashes["wos:player:player_42:state"]
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
