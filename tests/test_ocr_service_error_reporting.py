"""Integration-light tests for the OCR FastAPI service.

These tests exercise the endpoint function directly via ``TestClient`` and
monkey-patch out the PaddleOCR backend. They guard the per-region error
contract that the report flagged: a backend exception must produce
``error != None`` instead of being silently flattened to ``text=""``.
"""

from __future__ import annotations

import base64
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np
import pytest

# FastAPI is in the optional ``[ocr]`` extra — only installed inside the OCR
# container / dev env with that extra. Skip cleanly when absent so the default
# dev test run stays green.
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


def _b64_png(width: int = 40, height: int = 40) -> str:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def _client(monkeypatch: pytest.MonkeyPatch, paddle_behavior: Any) -> TestClient:
    """Build a TestClient with the Paddle backend replaced by *paddle_behavior*."""

    class _FakePaddle:
        def ocr(self, crop: np.ndarray) -> Any:
            return paddle_behavior(crop)

    monkeypatch.setattr(ocr_service, "get_paddle", lambda: _FakePaddle())

    def _extract(_ocr_out: Any) -> tuple[str, float]:
        # The real extract parses paddle's structured output; in these tests
        # we hand it back whatever paddle returned (a (text, conf) tuple).
        if isinstance(_ocr_out, tuple) and len(_ocr_out) == 2:
            return str(_ocr_out[0]), float(_ocr_out[1])
        return "", 0.0

    monkeypatch.setattr(ocr_service, "extract_text_confidence", _extract)
    return TestClient(ocr_service.app)


def test_ocr_endpoint_reports_per_region_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paddle exception on one region must be returned as ``error != None``
    on that region only — and must not 500 the entire request."""

    call_count = {"n": 0}

    def _paddle(_crop: np.ndarray) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("paddle blew up")
        return ("hello", 0.95)

    client = _client(monkeypatch, _paddle)
    resp = client.post(
        "/ocr",
        json={
            "image_b64": _b64_png(),
            "regions": [
                {"region_id": "a", "x": 0, "y": 0, "w": 20, "h": 20},
                {"region_id": "b", "x": 0, "y": 0, "w": 20, "h": 20},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_rid = {item["region_id"]: item for item in body}

    assert by_rid["a"]["text"] == ""
    assert by_rid["a"]["confidence"] == 0.0
    assert by_rid["a"]["error"] and "paddle blew up" in by_rid["a"]["error"]

    assert by_rid["b"]["text"] == "hello"
    assert by_rid["b"]["confidence"] == pytest.approx(0.95)
    assert by_rid["b"]["error"] is None


def test_metrics_endpoint_counts_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/metrics`` must reflect failed-region count so an operator can see
    a degraded backend without scraping logs."""

    def _always_fail(_crop: np.ndarray) -> Any:
        raise RuntimeError("nope")

    client = _client(monkeypatch, _always_fail)
    resp = client.post(
        "/ocr",
        json={
            "image_b64": _b64_png(),
            "regions": [
                {"region_id": "a", "x": 0, "y": 0, "w": 20, "h": 20},
                {"region_id": "b", "x": 0, "y": 0, "w": 20, "h": 20},
            ],
        },
    )
    assert resp.status_code == 200

    metrics = client.get("/metrics").json()
    assert metrics["ocr_requests_total"] == 1
    assert metrics["ocr_regions_total"] == 2
    assert metrics["ocr_failed_regions_total"] == 2
    assert metrics["ocr_request_ms_last"] >= 0.0
