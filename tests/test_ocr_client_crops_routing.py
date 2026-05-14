"""Adaptive endpoint routing: ``OcrClient`` picks ``/ocr_crops`` when the
total area of unique crops is small relative to the full frame, and falls
back to ``/ocr`` (legacy full-frame + bboxes) otherwise.

Why: encoding the full 720x1280 framebuffer in PNG and base64 just to OCR
a 50x30 countdown timer wastes ~50KB on the wire per OCR call. With
``/ocr_crops`` the client sends only the patch (sub-1KB). For OCR tasks
that already cover most of the frame (full-page reads, dense grids) the
per-crop PNG header overhead outweighs the savings, so the threshold
keeps those on the legacy path.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import cv2  # type: ignore[import-untyped]
import httpx
import numpy as np
import pytest

from layout.types import Region
from ocr.client import OcrClient


@pytest.fixture(autouse=True)
def _isolate_cache() -> Any:
    OcrClient.clear_cache()
    OcrClient._crops_endpoint_unavailable = False
    yield
    OcrClient.clear_cache()
    OcrClient._crops_endpoint_unavailable = False


def _img(h: int, w: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def _record_transport(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Install a MockTransport that captures every request, returning a
    canned response keyed by ``region_id``."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = json.loads(request.read().decode())
        regs = body.get("regions") or []
        items = [
            {"region_id": r["region_id"], "text": f"T-{r['region_id']}", "confidence": 0.9}
            for r in regs
        ]
        return httpx.Response(200, json=items)

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)
    return captured


@pytest.mark.asyncio
async def test_small_region_routes_to_ocr_crops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """50x30 region on a 720x1280 frame → 0.16% of frame → use crops mode."""
    captured = _record_transport(monkeypatch)
    image = _img(1280, 720)
    region = Region(x=40, y=120, w=50, h=30)

    res = await OcrClient().ocr_regions(image, [region], region_ids=["timer"])

    assert len(captured) == 1
    assert captured[0].url.path == "/ocr_crops"
    body = json.loads(captured[0].read().decode())
    assert "image_b64" not in body, "crops mode must not ship the full frame"
    assert [r["region_id"] for r in body["regions"]] == ["timer"]

    # The crop is a real PNG of the requested patch.
    crop_bytes = base64.b64decode(body["regions"][0]["image_b64"])
    decoded = cv2.imdecode(np.frombuffer(crop_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape == (30, 50, 3)

    assert res[0].region_id == "timer"
    assert res[0].text == "T-timer"


@pytest.mark.asyncio
async def test_large_region_routes_to_full_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A region covering >50% of the frame falls back to ``/ocr`` — encoding
    one full PNG is cheaper than crops with their own PNG headers."""
    captured = _record_transport(monkeypatch)
    image = _img(1280, 720)
    region = Region(x=0, y=0, w=720, h=900)  # 0.703 of frame

    await OcrClient().ocr_regions(image, [region], region_ids=["full"])

    assert len(captured) == 1
    assert captured[0].url.path == "/ocr"
    body = json.loads(captured[0].read().decode())
    assert "image_b64" in body, "full mode must ship the framebuffer"
    assert body["regions"][0] == {
        "region_id": "full",
        "x": 0,
        "y": 0,
        "w": 720,
        "h": 900,
    }


@pytest.mark.asyncio
async def test_many_small_regions_stay_on_crops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Common case: many narrow stat cells on the HUD. Total area stays a
    small fraction of the frame, so crops mode wins even with high count."""
    captured = _record_transport(monkeypatch)
    image = _img(1280, 720)
    regions = [Region(x=10 + i * 50, y=20, w=40, h=24) for i in range(12)]
    rids = [f"cell_{i}" for i in range(len(regions))]

    await OcrClient().ocr_regions(image, regions, region_ids=rids)

    assert len(captured) == 1
    assert captured[0].url.path == "/ocr_crops"
    body = json.loads(captured[0].read().decode())
    assert len(body["regions"]) == 12
    assert sorted(r["region_id"] for r in body["regions"]) == sorted(rids)
    for r in body["regions"]:
        assert "image_b64" in r
        assert "x" not in r and "y" not in r


@pytest.mark.asyncio
async def test_threshold_boundary_picks_full_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At exactly the threshold we fall back to full mode — strict ``<``
    keeps the boundary deterministic instead of toggling on float noise."""
    captured = _record_transport(monkeypatch)
    image = _img(100, 100)  # 10_000 px full area; threshold 0.5 → 5_000 px
    region = Region(x=0, y=0, w=100, h=50)  # exactly 5_000 px

    await OcrClient().ocr_regions(image, [region], region_ids=["edge"])

    assert captured[0].url.path == "/ocr"


@pytest.mark.asyncio
async def test_crops_mode_response_stitched_by_region_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crops mode keeps the same response-by-region_id stitching as ``/ocr``:
    backend reordering doesn't mis-attribute results across input slots."""
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        regs = body.get("regions") or []
        items = [
            {"region_id": r["region_id"], "text": f"T-{r['region_id']}", "confidence": 0.9}
            for r in regs
        ]
        items.reverse()  # simulate backend reordering
        return httpx.Response(200, json=items)

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    image = _img(1280, 720)
    regions = [
        Region(x=10, y=10, w=40, h=20),
        Region(x=200, y=300, w=60, h=20),
        Region(x=400, y=600, w=80, h=20),
    ]
    rids = ["alpha", "beta", "gamma"]
    res = await OcrClient().ocr_regions(image, regions, region_ids=rids)

    by_rid = {r.region_id: r for r in res}
    assert by_rid["alpha"].text == "T-alpha"
    assert by_rid["beta"].text == "T-beta"
    assert by_rid["gamma"].text == "T-gamma"


@pytest.mark.asyncio
async def test_crops_mode_within_batch_dedup_still_collapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hash-based dedup that collapses identical patches across input
    slots must keep working in crops mode — we still send one crop per
    unique BLAKE2b key, then fan the result out to every slot."""
    captured = _record_transport(monkeypatch)
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    image[100:140, 100:140] = (50, 60, 70)
    image[200:240, 200:240] = (50, 60, 70)
    # Two distinct slots, identical pixels → must reduce to 1 unique crop.
    regions = [
        Region(x=100, y=100, w=40, h=40),
        Region(x=200, y=200, w=40, h=40),
    ]

    res = await OcrClient().ocr_regions(image, regions, region_ids=["a", "b"])

    assert captured[0].url.path == "/ocr_crops"
    body = json.loads(captured[0].read().decode())
    assert len(body["regions"]) == 1, "duplicate-pixel slots must coalesce"
    # Both original slots get a result.
    assert {r.region_id for r in res} == {"a", "b"}


@pytest.mark.asyncio
async def test_404_on_crops_endpoint_falls_back_to_full_and_latches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old service that doesn't know ``/ocr_crops`` returns 404 → the client
    must retry on ``/ocr`` in the same call AND latch the unavailability so
    later requests skip the doomed endpoint entirely (avoids paying the 404
    round-trip per OCR call until a process restart).
    """
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/ocr_crops":
            return httpx.Response(404, json={"detail": "Not Found"})
        body = json.loads(request.read().decode())
        regs = body.get("regions") or []
        items = [
            {"region_id": r["region_id"], "text": f"T-{r['region_id']}", "confidence": 0.8}
            for r in regs
        ]
        return httpx.Response(200, json=items)

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    image = _img(1280, 720)
    region = Region(x=40, y=120, w=50, h=30)  # tiny → would pick crops

    # First call: tries /ocr_crops, gets 404, retries on /ocr, returns result.
    res1 = await OcrClient().ocr_regions(image, [region], region_ids=["timer"])
    assert res1[0].text == "T-timer"
    assert [r.url.path for r in captured] == ["/ocr_crops", "/ocr"]
    assert OcrClient._crops_endpoint_unavailable is True

    # Second call (same process): must skip /ocr_crops entirely.
    OcrClient.clear_cache()  # bust the result cache so we hit the wire again
    captured.clear()
    other = Region(x=200, y=400, w=60, h=30)
    res2 = await OcrClient().ocr_regions(image, [other], region_ids=["other"])
    assert res2[0].text == "T-other"
    assert [r.url.path for r in captured] == ["/ocr"]
