"""Backend dispatch of the ``preprocess`` field on OCR requests.

Pins the per-region routing in ``ocr.service._ocr_one_crop``:

* ``preprocess: enhance`` runs the crop through ``enhance_for_ocr`` before
  paddle. A failure in the preprocess step degrades to the raw crop (not a
  request-level 500) so a CLAHE bug on one frame doesn't take everything down.
* ``preprocess: fast_line`` calls ``paddle.ocr(crop, det=False)`` — the cheap
  path for ``type: time`` / ``type: integer`` regions where the detection
  model is overhead on a single short text line.
* Missing / empty ``preprocess`` keeps the historical full-pipeline behavior.
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


def _b64_png(width: int = 40, height: int = 40) -> str:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def _install_recording_paddle(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[Any]]:
    """Patch ``get_paddle`` with a recorder; return the captured-call log."""
    calls: dict[str, list[Any]] = {"shapes": [], "kwargs": []}

    class _RecordingPaddle:
        def ocr(self, crop: np.ndarray, **kwargs: Any) -> Any:
            calls["shapes"].append(tuple(crop.shape))
            calls["kwargs"].append(dict(kwargs))
            # Return the det=False-style page-wrapped list of (text, conf)
            # tuples for the fast_line branch and the same for the default
            # path — the normalizer in service.py handles both.
            if kwargs.get("det") is False:
                return [[("01:30:00", 0.97)]]
            return [[[ [[0, 0], [10, 0], [10, 10], [0, 10]], ("default-text", 0.91)]]]

    monkeypatch.setattr(ocr_service, "get_paddle", lambda: _RecordingPaddle())
    return calls


def test_fast_line_invokes_paddle_with_det_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``preprocess: fast_line`` ⇒ ``paddle.ocr(..., det=False)``.

    Detection model is the heaviest piece on tiny crops; locking the kwarg
    here keeps the cheap-path promise honest.
    """
    calls = _install_recording_paddle(monkeypatch)
    client = TestClient(ocr_service.app)
    resp = client.post(
        "/ocr",
        json={
            "image_b64": _b64_png(),
            "regions": [
                {
                    "region_id": "timer",
                    "x": 0,
                    "y": 0,
                    "w": 20,
                    "h": 20,
                    "preprocess": "fast_line",
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["region_id"] == "timer"
    assert body[0]["text"] == "01:30:00"
    assert body[0]["confidence"] == pytest.approx(0.97)
    assert calls["kwargs"] == [{"det": False}]


def test_no_preprocess_keeps_full_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default request (no ``preprocess``) calls paddle with no kwargs —
    backward-compat with the pre-preprocess client.
    """
    calls = _install_recording_paddle(monkeypatch)
    client = TestClient(ocr_service.app)
    resp = client.post(
        "/ocr",
        json={
            "image_b64": _b64_png(),
            "regions": [{"region_id": "r0", "x": 0, "y": 0, "w": 20, "h": 20}],
        },
    )
    assert resp.status_code == 200
    assert calls["kwargs"] == [{}]


def test_enhance_preprocess_does_not_pass_det_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``preprocess: enhance`` runs the image transform but does NOT switch
    to ``det=False`` — those are two independent pipelines and combining
    them is left for a future deliberate change."""
    calls = _install_recording_paddle(monkeypatch)
    client = TestClient(ocr_service.app)
    resp = client.post(
        "/ocr",
        json={
            "image_b64": _b64_png(),
            "regions": [
                {
                    "region_id": "r0",
                    "x": 0,
                    "y": 0,
                    "w": 20,
                    "h": 20,
                    "preprocess": "enhance",
                },
            ],
        },
    )
    assert resp.status_code == 200
    assert calls["kwargs"] == [{}]
    # Crop shape changes: enhance returns a 2× upscaled grayscale image
    # (cv2 reshapes back to 3-channel via the downstream pipeline). The
    # important assertion is that paddle got *something*, with the same
    # det/rec flags as the default path.
    assert calls["shapes"][0][:2] != (20, 20) or len(calls["shapes"][0]) == 2, (
        f"enhance must reshape the crop, got {calls['shapes'][0]}"
    )


def test_unknown_preprocess_value_falls_through_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future-proofing: the backend doesn't reject unknown ``preprocess``
    tags; it silently runs the default pipeline. Lets the client roll out a
    new value before the backend learns it without breaking requests.
    """
    calls = _install_recording_paddle(monkeypatch)
    client = TestClient(ocr_service.app)
    resp = client.post(
        "/ocr",
        json={
            "image_b64": _b64_png(),
            "regions": [
                {
                    "region_id": "r0",
                    "x": 0,
                    "y": 0,
                    "w": 20,
                    "h": 20,
                    "preprocess": "some_future_pipeline",
                },
            ],
        },
    )
    assert resp.status_code == 200
    assert calls["kwargs"] == [{}]


