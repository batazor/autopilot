from __future__ import annotations

import asyncio
from typing import Any

import httpx
import numpy as np
import pytest

from layout.types import Region
from ocr.client import OcrClient


@pytest.fixture(autouse=True)
def _isolate_cache() -> Any:
    OcrClient.clear_cache()
    yield
    OcrClient.clear_cache()


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, counter: list[int]) -> None:
    """Route OcrClient HTTP calls through MockTransport so we can count them."""

    def _handler(request: httpx.Request) -> httpx.Response:
        counter[0] += 1
        body = request.read()
        import json as _json

        payload = _json.loads(body.decode())
        regs = payload.get("regions") or []
        return httpx.Response(
            200,
            json=[
                {"region_id": r["region_id"], "text": f"text-for-{r['region_id']}", "confidence": 0.9}
                for r in regs
            ],
        )

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)


def _img(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(100, 100, 3), dtype=np.uint8)


def test_identical_pixels_serve_from_cache_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = [0]
    _install_mock_transport(monkeypatch, calls)
    client = OcrClient()
    img = _img(1)
    region = Region(10, 10, 30, 30)

    async def run() -> None:
        r1 = await client.ocr_regions(img, [region], region_ids=["page_title"])
        r2 = await client.ocr_regions(img, [region], region_ids=["page_title"])
        assert r1[0].text == r2[0].text == "text-for-page_title"

    asyncio.run(run())
    # Only one HTTP call — the second was served by the cache.
    assert calls[0] == 1


def test_changed_pixels_bypass_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = [0]
    _install_mock_transport(monkeypatch, calls)
    client = OcrClient()
    region = Region(10, 10, 30, 30)

    async def run() -> None:
        await client.ocr_regions(_img(1), [region], region_ids=["t"])
        # Different pixels → different patch hash → fresh OCR.
        await client.ocr_regions(_img(2), [region], region_ids=["t"])

    asyncio.run(run())
    assert calls[0] == 2


def test_partial_hit_sends_only_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = [0]
    last_payload_sizes: list[int] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        calls[0] += 1
        regs = _json.loads(request.read().decode()).get("regions") or []
        last_payload_sizes.append(len(regs))
        return httpx.Response(
            200,
            json=[
                {"region_id": r["region_id"], "text": f"t-{r['region_id']}", "confidence": 1.0}
                for r in regs
            ],
        )

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    client = OcrClient()
    img = _img(3)
    r_a = Region(0, 0, 20, 20)
    r_b = Region(40, 40, 20, 20)

    async def run() -> None:
        await client.ocr_regions(img, [r_a], region_ids=["a"])
        out = await client.ocr_regions(img, [r_a, r_b], region_ids=["a", "b"])
        assert {o.region_id for o in out} == {"a", "b"}

    asyncio.run(run())
    assert calls[0] == 2
    # Second call sent only the miss (b).
    assert last_payload_sizes == [1, 1]


def test_ttl_expires_cached_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = [0]
    _install_mock_transport(monkeypatch, calls)
    client = OcrClient()
    img = _img(4)
    region = Region(5, 5, 10, 10)

    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    monkeypatch.setattr("ocr.client.time.monotonic", _now)

    async def run() -> None:
        await client.ocr_regions(img, [region], region_ids=["t"])
        fake_now[0] += OcrClient._OCR_CACHE_TTL_S + 0.1
        await client.ocr_regions(img, [region], region_ids=["t"])

    asyncio.run(run())
    assert calls[0] == 2
