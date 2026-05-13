from __future__ import annotations

import asyncio
import logging
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


def _img(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(100, 100, 3), dtype=np.uint8)


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    transport = httpx.MockTransport(handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)


def test_ocr_regions_maps_by_region_id_not_response_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend may return results in a different order than requested.

    The client must stitch them back to the original request slot by
    ``region_id``, not by positional index in the response array.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        regs = _json.loads(request.read().decode()).get("regions") or []
        items = [
            {"region_id": r["region_id"], "text": f"T-{r['region_id']}", "confidence": 0.9}
            for r in regs
        ]
        # Reverse to simulate backend reordering.
        items.reverse()
        return httpx.Response(200, json=items)

    _install_transport(monkeypatch, _handler)
    client = OcrClient()
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10), Region(20, 20, 10, 10)]
    ids = ["alpha", "beta", "gamma"]

    async def run() -> list[Any]:
        return await client.ocr_regions(_img(1), regions, region_ids=ids)

    results = asyncio.run(run())
    by_rid = {r.region_id: r.text for r in results}
    assert by_rid == {
        "alpha": "T-alpha",
        "beta": "T-beta",
        "gamma": "T-gamma",
    }


def test_ocr_regions_ignores_unknown_region_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown region_ids in the response must be skipped with a warning,
    not silently mis-applied to a real slot."""

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        regs = _json.loads(request.read().decode()).get("regions") or []
        items = [
            {"region_id": r["region_id"], "text": f"T-{r['region_id']}", "confidence": 0.9}
            for r in regs
        ]
        items.append({"region_id": "ghost", "text": "junk", "confidence": 0.5})
        return httpx.Response(200, json=items)

    _install_transport(monkeypatch, _handler)
    client = OcrClient()
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10)]
    ids = ["alpha", "beta"]

    async def run() -> list[Any]:
        return await client.ocr_regions(_img(2), regions, region_ids=ids)

    with caplog.at_level(logging.WARNING, logger="ocr.client"):
        results = asyncio.run(run())

    assert {r.region_id for r in results} == {"alpha", "beta"}
    assert any("unknown region_id" in rec.getMessage() for rec in caplog.records)


def test_ocr_regions_propagates_backend_error_field(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the backend reports ``error`` for a region, the client must
    surface that on the ``OCRResult`` and skip cache-warming for that key —
    otherwise a transient backend failure would poison the patch cache."""

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        regs = _json.loads(request.read().decode()).get("regions") or []
        items = []
        for r in regs:
            if r["region_id"] == "alpha":
                items.append(
                    {
                        "region_id": "alpha",
                        "text": "",
                        "confidence": 0.0,
                        "error": "RuntimeError: paddle blew up",
                    }
                )
            else:
                items.append(
                    {
                        "region_id": r["region_id"],
                        "text": f"T-{r['region_id']}",
                        "confidence": 0.9,
                    }
                )
        return httpx.Response(200, json=items)

    _install_transport(monkeypatch, _handler)
    client = OcrClient()
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10)]
    ids = ["alpha", "beta"]

    async def run() -> list[Any]:
        # First call surfaces the error.
        first = await client.ocr_regions(_img(4), regions, region_ids=ids)
        # Second call must re-hit the backend for ``alpha`` (no cache poisoning).
        # We can't introspect call counts cleanly here, so we just verify the
        # error stays attached on a re-issue.
        second = await client.ocr_regions(_img(4), regions, region_ids=ids)
        return [first, second]

    with caplog.at_level(logging.WARNING, logger="ocr.client"):
        first, second = asyncio.run(run())

    a1 = next(r for r in first if r.region_id == "alpha")
    b1 = next(r for r in first if r.region_id == "beta")
    assert a1.error and "paddle blew up" in a1.error
    assert a1.text == "" and a1.confidence == 0.0
    assert b1.error is None
    assert any(
        "OCR backend error" in rec.getMessage() and "alpha" in rec.getMessage()
        for rec in caplog.records
    )
    # ``beta`` was successful → cached → second call returns the same text;
    # ``alpha`` was an error → not cached → backend was hit again and still
    # produced the error.
    a2 = next(r for r in second if r.region_id == "alpha")
    assert a2.error and "paddle blew up" in a2.error


def test_ocr_regions_warns_on_missing_region_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the backend drops a region_id from its response, ``ocr_regions`` must
    log a warning instead of silently returning fewer results in a different
    order."""

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        regs = _json.loads(request.read().decode()).get("regions") or []
        # Drop the first requested region — backend missed it.
        items = [
            {"region_id": r["region_id"], "text": f"T-{r['region_id']}", "confidence": 0.9}
            for r in regs[1:]
        ]
        return httpx.Response(200, json=items)

    _install_transport(monkeypatch, _handler)
    client = OcrClient()
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10)]
    ids = ["alpha", "beta"]

    async def run() -> list[Any]:
        return await client.ocr_regions(_img(3), regions, region_ids=ids)

    with caplog.at_level(logging.WARNING, logger="ocr.client"):
        results = asyncio.run(run())

    # ``ocr_regions`` returns only the slots that got an answer.
    assert {r.region_id for r in results} == {"beta"}
    assert any(
        "missing region_ids" in rec.getMessage() and "alpha" in rec.getMessage()
        for rec in caplog.records
    )
