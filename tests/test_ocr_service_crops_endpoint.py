"""Coverage for the ``/ocr_crops`` endpoint.

The endpoint exists so the bot doesn't ship a full 720x1280 screenshot when
it only needs OCR over a 50x30 countdown timer — clients pre-slice and
encode each crop, the backend OCRs them directly (no second slicing).

Asserts here:
* Decoded crops reach the (stubbed) PaddleOCR with the *crop* pixels,
  not a re-sliced view of some larger image.
* Response schema is identical to ``/ocr`` so callers don't branch on
  endpoint choice.
* A malformed crop on one region produces a per-region ``error`` and
  bumps ``ocr_failed_regions_total`` — without 500'ing the whole batch.
* ``/metrics`` counts the request the same way ``/ocr`` does.
"""

from __future__ import annotations

import base64
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from ocr import service as ocr_service  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_metrics() -> Any:
    with ocr_service._metrics_lock:
        for key in list(ocr_service._metrics):
            if key.endswith("_total") or key.endswith("_sum"):
                ocr_service._metrics[key] = 0
            else:
                ocr_service._metrics[key] = 0.0
    yield


def _b64_png_solid(width: int, height: int, color: tuple[int, int, int]) -> str:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :] = color
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def _client(monkeypatch: pytest.MonkeyPatch, paddle_behavior: Any) -> TestClient:
    class _FakePaddle:
        def ocr(self, crop: np.ndarray) -> Any:
            return paddle_behavior(crop)

    monkeypatch.setattr(ocr_service, "get_paddle", lambda: _FakePaddle())

    def _extract(_ocr_out: Any) -> tuple[str, float]:
        if isinstance(_ocr_out, tuple) and len(_ocr_out) == 2:
            return str(_ocr_out[0]), float(_ocr_out[1])
        return "", 0.0

    monkeypatch.setattr(ocr_service, "extract_text_confidence", _extract)
    return TestClient(ocr_service.app)


def test_ocr_crops_decodes_each_crop_and_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two crops in → two results out, in the same order; paddle sees the
    decoded crops with their actual dimensions (not a slice of a larger
    image)."""
    seen_shapes: list[tuple[int, int, int]] = []

    def _paddle(crop: np.ndarray) -> Any:
        seen_shapes.append(crop.shape)
        h = int(crop.shape[0])
        return (f"h={h}", 0.91)

    client = _client(monkeypatch, _paddle)
    resp = client.post(
        "/ocr_crops",
        json={
            "regions": [
                {"region_id": "small", "image_b64": _b64_png_solid(30, 20, (10, 10, 10))},
                {"region_id": "wide", "image_b64": _b64_png_solid(120, 40, (20, 20, 20))},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_rid = {item["region_id"]: item for item in body}

    assert by_rid["small"]["text"] == "h=20"
    assert by_rid["small"]["confidence"] == pytest.approx(0.91)
    assert by_rid["small"]["error"] is None
    assert by_rid["wide"]["text"] == "h=40"
    assert by_rid["wide"]["error"] is None

    # Paddle received the crop *as decoded*, not a slice of a larger image.
    assert sorted(s[:2] for s in seen_shapes) == [(20, 30), (40, 120)]


def test_ocr_crops_per_region_decode_error_does_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad base64 / corrupted PNG on one region must produce ``error != None``
    on that region; the rest of the batch still OCRs."""

    def _paddle(_crop: np.ndarray) -> Any:
        return ("ok", 0.8)

    client = _client(monkeypatch, _paddle)
    resp = client.post(
        "/ocr_crops",
        json={
            "regions": [
                {"region_id": "bad", "image_b64": "not-base64!!!"},
                {"region_id": "good", "image_b64": _b64_png_solid(20, 20, (5, 5, 5))},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    by_rid = {item["region_id"]: item for item in resp.json()}
    assert by_rid["bad"]["text"] == ""
    assert by_rid["bad"]["confidence"] == 0.0
    assert by_rid["bad"]["error"] and "decode_failed" in by_rid["bad"]["error"]
    assert by_rid["good"]["text"] == "ok"
    assert by_rid["good"]["error"] is None

    metrics = client.get("/metrics").json()
    assert metrics["ocr_failed_regions_total"] == 1


def test_ocr_crops_per_region_paddle_error_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paddle exception on one crop must surface as ``error != None`` —
    same per-region error contract as ``/ocr``, just over the crop path."""
    call_count = {"n": 0}

    def _paddle(_crop: np.ndarray) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("paddle blew up")
        return ("after-fail", 0.7)

    client = _client(monkeypatch, _paddle)
    resp = client.post(
        "/ocr_crops",
        json={
            "regions": [
                {"region_id": "a", "image_b64": _b64_png_solid(20, 20, (0, 0, 0))},
                {"region_id": "b", "image_b64": _b64_png_solid(20, 20, (0, 0, 0))},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    by_rid = {item["region_id"]: item for item in resp.json()}
    assert by_rid["a"]["error"] and "paddle blew up" in by_rid["a"]["error"]
    assert by_rid["a"]["text"] == ""
    assert by_rid["b"]["error"] is None
    assert by_rid["b"]["text"] == "after-fail"

    metrics = client.get("/metrics").json()
    assert metrics["ocr_failed_regions_total"] == 1


def test_ocr_crops_metrics_counted_same_as_full_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/ocr_crops`` must update the same counters as ``/ocr`` so the
    operator dashboard works regardless of which endpoint the client picked."""

    def _paddle(_crop: np.ndarray) -> Any:
        return ("x", 0.9)

    client = _client(monkeypatch, _paddle)
    resp = client.post(
        "/ocr_crops",
        json={
            "regions": [
                {"region_id": "a", "image_b64": _b64_png_solid(20, 20, (0, 0, 0))},
                {"region_id": "b", "image_b64": _b64_png_solid(20, 20, (0, 0, 0))},
                {"region_id": "c", "image_b64": _b64_png_solid(20, 20, (0, 0, 0))},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    metrics = client.get("/metrics").json()
    assert metrics["ocr_requests_total"] == 1
    assert metrics["ocr_regions_total"] == 3
    assert metrics["ocr_failed_regions_total"] == 0
    assert metrics["ocr_request_ms_last"] >= 0.0
