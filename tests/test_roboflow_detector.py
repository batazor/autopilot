"""Unit tests for the Roboflow inference client (no live server)."""
from __future__ import annotations

import httpx
import numpy as np
import pytest

from inference import roboflow_client
from inference.roboflow_client import (
    Detection,
    InferenceUnavailableError,
    RoboflowDetector,
)

_SAMPLE_BODY = {
    "predictions": [
        {"x": 100.0, "y": 200.0, "width": 40.0, "height": 20.0, "confidence": 0.91, "class": "fish"},
        {"x": 10.0, "y": 10.0, "width": 8.0, "height": 8.0, "confidence": 0.55, "class": "fish"},
    ],
    "image": {"width": 720, "height": 1280},
}


def _frame() -> np.ndarray:
    return np.zeros((1280, 720, 3), dtype=np.uint8)


def test_detection_geometry() -> None:
    d = Detection(x=100.0, y=200.0, width=40.0, height=20.0, confidence=0.9, class_name="fish")
    assert d.center == (100, 200)
    assert d.left == 80  # 100 - 40/2
    assert d.top == 190  # 200 - 20/2


def test_parse_skips_malformed() -> None:
    body = {"predictions": [{"x": "oops"}, _SAMPLE_BODY["predictions"][0]]}
    out = RoboflowDetector._parse(body)
    assert len(out) == 1
    assert out[0].class_name == "fish"


def test_parse_non_list_returns_empty() -> None:
    assert RoboflowDetector._parse({"predictions": None}) == []
    assert RoboflowDetector._parse({}) == []


def test_available_gate() -> None:
    assert RoboflowDetector(service_url="http://x:9001", model_id="m/1").available()
    assert not RoboflowDetector(service_url="", model_id="m/1").available()
    assert not RoboflowDetector(service_url="http://x:9001", model_id="").available()


@pytest.mark.asyncio
async def test_detect_raises_when_unconfigured() -> None:
    det = RoboflowDetector(service_url="", model_id="")
    with pytest.raises(InferenceUnavailableError):
        await det.detect(_frame())


@pytest.mark.asyncio
async def test_detect_empty_frame_returns_empty() -> None:
    det = RoboflowDetector(service_url="http://x:9001", model_id="m/1")
    assert await det.detect(np.zeros((0, 0, 3), dtype=np.uint8)) == []


@pytest.mark.asyncio
async def test_detect_sends_params_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json=_SAMPLE_BODY)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(roboflow_client.httpx, "AsyncClient", patched_client)

    det = RoboflowDetector(
        service_url="http://inference:9001",
        model_id="find-fish-ssnpa/6",
        api_key="SECRET",
        confidence=0.4,
    )
    out = await det.detect(_frame(), threshold=0.6)

    url = str(captured["url"])
    assert "find-fish-ssnpa/6" in url
    assert "api_key=SECRET" in url
    assert "confidence=0.6" in url  # explicit threshold overrides config default
    assert len(captured["body"]) > 0  # base64 image payload present

    assert len(out) == 2
    assert out[0].center == (100, 200)
    assert out[0].confidence == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_detect_wraps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(roboflow_client.httpx, "AsyncClient", patched_client)

    det = RoboflowDetector(service_url="http://x:9001", model_id="m/1")
    with pytest.raises(InferenceUnavailableError, match="HTTP 500"):
        await det.detect(_frame())
