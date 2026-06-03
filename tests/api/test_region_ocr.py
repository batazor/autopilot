from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from api.services import overlay_test
from ocr.client import OCRResult

if TYPE_CHECKING:
    from pathlib import Path

_AREA_DOC = {
    "version": 2,
    "screens": [
        {
            "screen_id": "dreamscape_memory",
            "ocr": "",
            "regions": [
                {
                    "name": "dreamscape_memory.1",
                    "action": "text",
                    "type": "string",
                    "threshold": 0.8,
                    "bbox": {"x": 8, "y": 89, "width": 24, "height": 4},
                },
                {
                    "name": "dreamscape_memory.2",
                    "action": "text",
                    "type": "string",
                    "threshold": 0.8,
                    "bbox": {"x": 38, "y": 89, "width": 24, "height": 4},
                },
            ],
        }
    ],
}


class _FakeOcr:
    """Returns one canned OCRResult per region_id."""

    def __init__(self, by_region: dict[str, OCRResult]) -> None:
        self._by_region = by_region

    async def ocr_region(self, _image, _region, *, region_id=None, **_kw) -> OCRResult:
        return self._by_region.get(
            region_id or "", OCRResult(region_id=region_id or "", text="", confidence=0.0)
        )


def _patch_common(monkeypatch, tmp_path: Path, *, frame: np.ndarray | None) -> None:
    monkeypatch.setattr(overlay_test, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(overlay_test, "load_area_doc", lambda _repo: _AREA_DOC)
    if frame is None:
        monkeypatch.setattr(
            overlay_test, "load_preview_bytes", lambda **_k: (None, "", None)
        )
    else:
        ok, encoded = cv2.imencode(".png", frame)
        assert ok
        monkeypatch.setattr(
            overlay_test,
            "load_preview_bytes",
            lambda **_k: (encoded.tobytes(), "temporal/bs1.png", 1.0),
        )
    monkeypatch.setattr(
        overlay_test, "load_rolling_instance_preview", lambda _i: (None, "", None)
    )
    monkeypatch.setattr(overlay_test, "active_player_state_flat", lambda **_k: {})
    monkeypatch.setattr(
        "dashboard.redis_client.get_instance_state",
        lambda *_a, **_k: {"current_screen": "dreamscape_memory", "active_player": "p1"},
    )


def test_region_ocr_returns_text_per_region(tmp_path: Path, monkeypatch) -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    _patch_common(monkeypatch, tmp_path, frame=frame)
    monkeypatch.setattr(
        overlay_test,
        "get_ocr_client",
        lambda: _FakeOcr(
            {
                "dreamscape_memory.1": OCRResult(
                    region_id="dreamscape_memory.1", text="Book", confidence=0.95
                ),
                "dreamscape_memory.2": OCRResult(
                    region_id="dreamscape_memory.2", text="Wolf", confidence=0.5
                ),
            }
        ),
    )

    result = overlay_test.run_region_ocr(
        client=object(),
        instance_id="bs1",
        regions=["dreamscape_memory.1", "dreamscape_memory.2"],
    )

    assert result["current_screen"] == "dreamscape_memory"
    rows = {r["region"]: r for r in result["rows"]}
    assert rows["dreamscape_memory.1"]["text"] == "Book"
    assert rows["dreamscape_memory.1"]["status"] == "ok"
    assert rows["dreamscape_memory.1"]["low_confidence"] is False
    assert isinstance(rows["dreamscape_memory.1"]["duration_ms"], float)
    # conf 0.5 < threshold 0.8 -> flagged low confidence but still ok text
    assert rows["dreamscape_memory.2"]["text"] == "Wolf"
    assert rows["dreamscape_memory.2"]["low_confidence"] is True


def test_region_ocr_unknown_region_is_no_region(tmp_path: Path, monkeypatch) -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    _patch_common(monkeypatch, tmp_path, frame=frame)
    monkeypatch.setattr(overlay_test, "get_ocr_client", lambda: _FakeOcr({}))

    result = overlay_test.run_region_ocr(
        client=object(), instance_id="bs1", regions=["does.not.exist"]
    )
    assert result["rows"][0]["status"] == "no_region"
    assert result["rows"][0]["text"] == ""


def test_region_ocr_no_frame(tmp_path: Path, monkeypatch) -> None:
    _patch_common(monkeypatch, tmp_path, frame=None)
    monkeypatch.setattr(overlay_test, "get_ocr_client", lambda: _FakeOcr({}))

    result = overlay_test.run_region_ocr(
        client=object(), instance_id="bs1", regions=["dreamscape_memory.1"]
    )
    assert result["preview"]["available"] is False
    assert result["rows"][0]["status"] == "no_frame"


def test_region_ocr_test_runs_detection_and_ocr_on_upload(
    tmp_path: Path, monkeypatch
) -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", frame)
    assert ok

    monkeypatch.setattr(overlay_test, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(overlay_test, "load_area_doc", lambda _repo: _AREA_DOC)
    monkeypatch.setattr(overlay_test, "active_player_state_flat", lambda **_k: {})
    monkeypatch.setattr(
        overlay_test, "_detect_screen_on_frame", lambda _img, **_k: ("dreamscape_memory", 3)
    )
    monkeypatch.setattr(
        overlay_test,
        "get_ocr_client",
        lambda: _FakeOcr(
            {
                "dreamscape_memory.1": OCRResult(
                    region_id="dreamscape_memory.1", text="Book", confidence=0.95
                )
            }
        ),
    )

    result = overlay_test.run_region_ocr_test(
        client=object(),
        instance_id="bs1",
        image_bytes=encoded.tobytes(),
        regions=["dreamscape_memory.1"],
    )

    assert result["detected_screen"] == "dreamscape_memory"
    assert result["screen_source"] == "detected"
    assert result["preview"]["available"] is True
    assert result["rows"][0]["text"] == "Book"
    assert isinstance(result["rows"][0]["duration_ms"], float)


def test_region_ocr_test_handles_undecodable_bytes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(overlay_test, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(overlay_test, "load_area_doc", lambda _repo: _AREA_DOC)
    monkeypatch.setattr(overlay_test, "active_player_state_flat", lambda **_k: {})
    monkeypatch.setattr(overlay_test, "get_ocr_client", lambda: _FakeOcr({}))

    result = overlay_test.run_region_ocr_test(
        client=object(),
        instance_id="bs1",
        image_bytes=b"not an image",
        regions=["dreamscape_memory.1"],
    )
    assert result["detected_screen"] == ""
    assert result["preview"]["available"] is False
    assert result["rows"][0]["status"] == "no_frame"


def test_region_ocr_empty_text(tmp_path: Path, monkeypatch) -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    _patch_common(monkeypatch, tmp_path, frame=frame)
    monkeypatch.setattr(
        overlay_test,
        "get_ocr_client",
        lambda: _FakeOcr(
            {
                "dreamscape_memory.1": OCRResult(
                    region_id="dreamscape_memory.1", text="  ", confidence=0.0
                )
            }
        ),
    )

    result = overlay_test.run_region_ocr(
        client=object(), instance_id="bs1", regions=["dreamscape_memory.1"]
    )
    assert result["rows"][0]["status"] == "empty"
