from __future__ import annotations

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


def _img(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(100, 100, 3), dtype=np.uint8)


def _install_crop_ocr(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, str | None, tuple[int, ...]]],
) -> None:
    async def _fake_ocr_crop(
        self: OcrClient,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
    ) -> OCRResult:
        calls.append((region_id, preprocess, tuple(crop.shape)))
        prefix = "raw" if not preprocess else str(preprocess)
        return OCRResult(region_id=region_id, text=f"{prefix}-{region_id}", confidence=0.9)

    monkeypatch.setattr(OcrClient, "_ocr_crop", _fake_ocr_crop)


@pytest.mark.asyncio
async def test_identical_pixels_serve_from_cache_without_rerunning_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(1)
    region = Region(10, 10, 30, 30)

    r1 = await client.ocr_regions(img, [region], region_ids=["page_title"])
    r2 = await client.ocr_regions(img, [region], region_ids=["page_title"])

    assert r1[0].text == r2[0].text == "raw-page_title"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_changed_pixels_bypass_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    region = Region(10, 10, 30, 30)

    await client.ocr_regions(_img(1), [region], region_ids=["t"])
    await client.ocr_regions(_img(2), [region], region_ids=["t"])

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_partial_hit_runs_ocr_only_for_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(3)
    r_a = Region(0, 0, 20, 20)
    r_b = Region(40, 40, 20, 20)

    await client.ocr_regions(img, [r_a], region_ids=["a"])
    out = await client.ocr_regions(img, [r_a, r_b], region_ids=["a", "b"])

    assert {o.region_id for o in out} == {"a", "b"}
    assert [rid for rid, _, _ in calls] == ["a", "b"]


@pytest.mark.asyncio
async def test_within_batch_identical_patches_collapse_to_one_ocr_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(7)
    region = Region(10, 10, 30, 30)
    ids = [f"slot_{i}" for i in range(50)]

    results = await client.ocr_regions(img, [region] * 50, region_ids=ids)

    assert len(calls) == 1
    assert calls[0][0] == "slot_0"
    assert len(results) == 50
    assert {r.region_id for r in results} == set(ids)
    assert all(r.text == "raw-slot_0" and r.confidence == 0.9 for r in results)


@pytest.mark.asyncio
async def test_within_batch_mixed_unique_and_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(9)
    r_dup = Region(0, 0, 20, 20)
    r_alt = Region(30, 30, 20, 20)
    regions = [r_dup, r_alt, r_dup, r_dup, r_alt]
    ids = ["dup_a", "alt_1", "dup_b", "dup_c", "alt_2"]

    results = await client.ocr_regions(img, regions, region_ids=ids)

    assert [rid for rid, _, _ in calls] == ["dup_a", "alt_1"]
    by_rid = {r.region_id: r for r in results}
    assert by_rid["dup_a"].text == "raw-dup_a"
    assert by_rid["dup_b"].text == "raw-dup_a"
    assert by_rid["dup_c"].text == "raw-dup_a"
    assert by_rid["alt_1"].text == "raw-alt_1"
    assert by_rid["alt_2"].text == "raw-alt_1"


@pytest.mark.asyncio
async def test_preprocess_flag_does_not_share_cache_with_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(13)
    region = Region(10, 10, 30, 30)

    raw = await client.ocr_regions(img, [region], region_ids=["a"])
    enhanced = await client.ocr_regions(img, [region], region_ids=["a"], region_preprocess=["enhance"])
    raw_again = await client.ocr_regions(img, [region], region_ids=["a"])
    enh_again = await client.ocr_regions(img, [region], region_ids=["a"], region_preprocess=["enhance"])

    assert len(calls) == 2
    assert [pre for _, pre, _ in calls] == [None, "enhance"]
    assert raw[0].text == raw_again[0].text == "raw-a"
    assert enhanced[0].text == enh_again[0].text == "enhance-a"


@pytest.mark.asyncio
async def test_ttl_expires_cached_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None, tuple[int, ...]]] = []
    _install_crop_ocr(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(4)
    region = Region(5, 5, 10, 10)
    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    monkeypatch.setattr("ocr.client.time.monotonic", _now)

    await client.ocr_regions(img, [region], region_ids=["t"])
    fake_now[0] += OcrClient._OCR_CACHE_TTL_S + 0.1
    await client.ocr_regions(img, [region], region_ids=["t"])

    assert len(calls) == 2
