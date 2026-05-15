from __future__ import annotations

import asyncio
from typing import Any

import httpx
import numpy as np
import pytest

from config.loader import get_settings
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
    client = OcrClient(get_settings())
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
    client = OcrClient(get_settings())
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

    client = OcrClient(get_settings())
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


def test_within_batch_identical_patches_collapse_to_one_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated identical bboxes in one ``ocr_regions`` call hit the backend once.

    Reproduces ``screen_verify.yaml`` first-pass cost: 141 ``page.heroes.unit.name``
    cells with identical pixels would all fan out to paddle on the cold call —
    the TTL cache only collapses repeats *across* calls. Within-batch dedup by
    ``patch_hash`` keeps the backend payload one entry, then fans the verdict
    back out to every caller-supplied ``region_id``.
    """
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
                {"region_id": r["region_id"], "text": "SAME", "confidence": 0.95}
                for r in regs
            ],
        )

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    client = OcrClient(get_settings())
    img = _img(7)
    region = Region(10, 10, 30, 30)
    regions = [region] * 50
    ids = [f"slot_{i}" for i in range(50)]

    async def run() -> list[Any]:
        return await client.ocr_regions(img, regions, region_ids=ids)

    results = asyncio.run(run())

    assert calls[0] == 1
    # Exactly one payload entry was sent to the backend despite 50 identical regions.
    assert last_payload_sizes == [1]
    # Every caller slot got a result keyed by its own region_id, all with the
    # backend's verdict for the unique patch.
    assert len(results) == 50
    assert {r.region_id for r in results} == set(ids)
    assert all(r.text == "SAME" and r.confidence == 0.95 for r in results)


def test_within_batch_mixed_unique_and_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unique patches go through, duplicates collapse — backend sees only the uniques.

    Backend distinguishes payloads by ``region_id``; if dedup leaks duplicate
    rids into the payload the test will fail on either the size check or the
    rid-set check (which verifies the fanout used per-slot rids, not the
    representative's rid).
    """
    last_payload_rids: list[list[str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        regs = _json.loads(request.read().decode()).get("regions") or []
        last_payload_rids.append([r["region_id"] for r in regs])
        return httpx.Response(
            200,
            json=[
                {
                    "region_id": r["region_id"],
                    "text": f"text-{r['region_id']}",
                    "confidence": 0.9,
                }
                for r in regs
            ],
        )

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    client = OcrClient(get_settings())
    img = _img(9)
    r_dup = Region(0, 0, 20, 20)
    r_alt = Region(30, 30, 20, 20)
    # Layout: dup_a, alt, dup_b, dup_c, alt_again — two unique patches, 5 slots.
    regions = [r_dup, r_alt, r_dup, r_dup, r_alt]
    ids = ["dup_a", "alt_1", "dup_b", "dup_c", "alt_2"]

    async def run() -> list[Any]:
        return await client.ocr_regions(img, regions, region_ids=ids)

    results = asyncio.run(run())

    assert len(last_payload_rids) == 1
    # Backend got two unique entries — the first occurrence of each patch.
    sent = last_payload_rids[0]
    assert len(sent) == 2
    assert set(sent) == {"dup_a", "alt_1"}

    by_rid = {r.region_id: r for r in results}
    assert set(by_rid) == set(ids)
    # Three duplicates share the ``dup_a`` text; two alts share the ``alt_1`` text.
    assert by_rid["dup_a"].text == "text-dup_a"
    assert by_rid["dup_b"].text == "text-dup_a"
    assert by_rid["dup_c"].text == "text-dup_a"
    assert by_rid["alt_1"].text == "text-alt_1"
    assert by_rid["alt_2"].text == "text-alt_1"


def test_within_batch_dedup_caches_once_per_unique_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up call hits the TTL cache — fan-out doesn't break cache warming.

    Confirms ``_cache_put`` runs once per unique patch hash (not per fanout
    slot), and the cached entry then serves every subsequent identical region
    in a follow-up batch — no backend hits at all on the second call.
    """
    calls: list[int] = [0]
    _install_mock_transport(monkeypatch, calls)
    client = OcrClient(get_settings())
    img = _img(11)
    region = Region(5, 5, 25, 25)

    async def run() -> None:
        await client.ocr_regions(
            img, [region] * 10, region_ids=[f"a{i}" for i in range(10)]
        )
        out = await client.ocr_regions(
            img, [region] * 4, region_ids=[f"b{i}" for i in range(4)]
        )
        assert len(out) == 4

    asyncio.run(run())
    assert calls[0] == 1


def test_preprocess_flag_does_not_share_cache_with_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``preprocess: enhance`` is part of the cache key, not just transport.

    Identical pixels run through ``enhance_for_ocr`` and the raw path can
    produce different OCR text, so they must occupy separate cache entries.
    Without folding ``preprocess`` into the patch hash the second call would
    serve the first's verdict regardless of which pipeline ran.
    """
    calls: list[int] = [0]
    seen_preprocess: list[str | None] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        calls[0] += 1
        regs = _json.loads(request.read().decode()).get("regions") or []
        seen_preprocess.append(regs[0].get("preprocess") if regs else None)
        return httpx.Response(
            200,
            json=[
                {
                    "region_id": r["region_id"],
                    "text": f"raw-{r['region_id']}" if "preprocess" not in r
                    else f"enh-{r['region_id']}",
                    "confidence": 0.95,
                }
                for r in regs
            ],
        )

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    client = OcrClient(get_settings())
    img = _img(13)
    region = Region(10, 10, 30, 30)

    async def run() -> tuple[list[Any], list[Any], list[Any]]:
        raw = await client.ocr_regions(img, [region], region_ids=["a"])
        enhanced = await client.ocr_regions(
            img, [region], region_ids=["a"], region_preprocess=["enhance"]
        )
        # Both should now be cached — third/fourth calls re-hit the cache.
        raw_again = await client.ocr_regions(img, [region], region_ids=["a"])
        enh_again = await client.ocr_regions(
            img, [region], region_ids=["a"], region_preprocess=["enhance"]
        )
        return raw + enhanced, raw_again, enh_again

    combined, raw_again, enh_again = asyncio.run(run())

    # Two distinct backend calls — the preprocess variant didn't piggy-back
    # on the raw cache entry.
    assert calls[0] == 2
    assert seen_preprocess == [None, "enhance"]
    raw_text = combined[0].text
    enh_text = combined[1].text
    assert raw_text != enh_text
    # Re-issued queries hit the cache, no extra HTTP calls.
    assert calls[0] == 2
    assert raw_again[0].text == raw_text
    assert enh_again[0].text == enh_text


def test_preprocess_omitted_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend payload omits ``preprocess`` entirely for raw regions.

    Older backends that predate the field would reject an unexpected key on
    every batch — keep the wire format byte-identical to the pre-preprocess
    shape when nothing opted in.
    """
    seen_keys: list[set[str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        regs = _json.loads(request.read().decode()).get("regions") or []
        seen_keys.append({k for k in regs[0]} if regs else set())
        return httpx.Response(
            200,
            json=[{"region_id": r["region_id"], "text": "x", "confidence": 0.9} for r in regs],
        )

    transport = httpx.MockTransport(_handler)

    async def _fake_http_client(self: OcrClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(OcrClient, "_http_client", _fake_http_client)

    client = OcrClient(get_settings())
    img = _img(15)
    region = Region(0, 0, 20, 20)

    async def run() -> None:
        await client.ocr_regions(img, [region], region_ids=["a"])

    asyncio.run(run())
    assert seen_keys, "handler not invoked"
    assert "preprocess" not in seen_keys[0]


def test_ttl_expires_cached_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = [0]
    _install_mock_transport(monkeypatch, calls)
    client = OcrClient(get_settings())
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
