from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pytest

from config.loader import get_settings
from layout.types import Region
from ocr.client import OcrClient, OCRResult


@pytest.fixture(autouse=True)
def _isolate_cache() -> Any:
    OcrClient.clear_cache()
    yield
    OcrClient.clear_cache()


def _img(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(100, 100, 3), dtype=np.uint8)


@pytest.mark.asyncio
async def test_ocr_regions_maps_by_region_id_not_response_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_ocr_crop(
        self: OcrClient,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        return OCRResult(region_id=region_id, text=f"T-{region_id}", confidence=0.9)

    monkeypatch.setattr(OcrClient, "_ocr_crop", _fake_ocr_crop)
    client = OcrClient(get_settings())
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10), Region(20, 20, 10, 10)]
    ids = ["alpha", "beta", "gamma"]

    results = await client.ocr_regions(_img(1), regions, region_ids=ids)

    by_rid = {r.region_id: r.text for r in results}
    assert by_rid == {
        "alpha": "T-alpha",
        "beta": "T-beta",
        "gamma": "T-gamma",
    }


@pytest.mark.asyncio
async def test_ocr_regions_ignores_unknown_region_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _fake_ocr_crop(
        self: OcrClient,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        return OCRResult(region_id="ghost" if region_id == "alpha" else region_id, text="x", confidence=0.9)

    monkeypatch.setattr(OcrClient, "_ocr_crop", _fake_ocr_crop)
    client = OcrClient(get_settings())
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10)]
    ids = ["alpha", "beta"]

    with caplog.at_level(logging.WARNING, logger="ocr.client"):
        results = await client.ocr_regions(_img(2), regions, region_ids=ids)

    assert {r.region_id for r in results} == {"beta"}
    assert any("unknown region_id" in rec.getMessage() for rec in caplog.records)
    assert any("missing region_ids" in rec.getMessage() and "alpha" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_ocr_regions_propagates_error_field_without_cache_poisoning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    async def _fake_ocr_crop(
        self: OcrClient,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        calls.append(region_id)
        if region_id == "alpha":
            return OCRResult(region_id="alpha", text="", confidence=0.0, error="RuntimeError: tesseract blew up")
        return OCRResult(region_id=region_id, text=f"T-{region_id}", confidence=0.9)

    monkeypatch.setattr(OcrClient, "_ocr_crop", _fake_ocr_crop)
    client = OcrClient(get_settings())
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10)]
    ids = ["alpha", "beta"]
    img = _img(4)

    with caplog.at_level(logging.WARNING, logger="ocr.client"):
        first = await client.ocr_regions(img, regions, region_ids=ids)
        second = await client.ocr_regions(img, regions, region_ids=ids)

    a1 = next(r for r in first if r.region_id == "alpha")
    b1 = next(r for r in first if r.region_id == "beta")
    a2 = next(r for r in second if r.region_id == "alpha")
    assert a1.error and "tesseract blew up" in a1.error
    assert b1.error is None
    assert a2.error and "tesseract blew up" in a2.error
    assert calls == ["alpha", "beta", "alpha"]
    assert any("OCR error" in rec.getMessage() and "alpha" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_ocr_regions_warns_on_missing_region_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _fake_ocr_crop(
        self: OcrClient,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        if region_id == "alpha":
            return OCRResult(region_id="", text="", confidence=0.0)
        return OCRResult(region_id=region_id, text=f"T-{region_id}", confidence=0.9)

    monkeypatch.setattr(OcrClient, "_ocr_crop", _fake_ocr_crop)
    client = OcrClient(get_settings())
    regions = [Region(0, 0, 10, 10), Region(10, 10, 10, 10)]
    ids = ["alpha", "beta"]

    with caplog.at_level(logging.WARNING, logger="ocr.client"):
        results = await client.ocr_regions(_img(3), regions, region_ids=ids)

    assert {r.region_id for r in results} == {"beta"}
    assert any("missing region_ids" in rec.getMessage() and "alpha" in rec.getMessage() for rec in caplog.records)
