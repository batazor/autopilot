from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

import cv2  # type: ignore[import-untyped]
import httpx
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from config.loader import get_settings
from layout.types import Region

logger = logging.getLogger(__name__)

# Highlight only the region **name** (region_id) in terminal OCR logs; bbox/conf/text stay plain.
_OCR_YELLOW = "\033[33m"
_OCR_RESET = "\033[0m"


def _ocr_tty_yellow_name(name: str) -> str:
    err = sys.stderr
    if not name or not getattr(err, "isatty", lambda: False)():
        return name
    return f"{_OCR_YELLOW}{name}{_OCR_RESET}"


@dataclass(frozen=True)
class OCRResult:
    region_id: str
    text: str
    confidence: float


class OcrClient:
    _clients: ClassVar[dict[tuple[str, float, int], httpx.AsyncClient]] = {}

    # Content-addressed cache: BLAKE2b of the bbox patch bytes → (ts, text, conf).
    # Pixels identical ⇒ OCR result identical, so the patch hash itself is a
    # correctness boundary (no need to invalidate on tap/swipe — pixel changes
    # already produce a different key).  TTL caps memory growth and is a safety
    # net against any edge case the hash misses.
    _OCR_CACHE_TTL_S: ClassVar[float] = 2.0
    _OCR_CACHE_MAX: ClassVar[int] = 256
    _cache: ClassVar[OrderedDict[bytes, tuple[float, str, float]]] = OrderedDict()

    @classmethod
    def _patch_hash(cls, image: np.ndarray, region: Region) -> bytes:
        hi, wi = int(image.shape[0]), int(image.shape[1])
        x1 = max(0, min(int(region.x), wi))
        y1 = max(0, min(int(region.y), hi))
        x2 = max(x1, min(int(region.x + region.w), wi))
        y2 = max(y1, min(int(region.y + region.h), hi))
        patch = image[y1:y2, x1:x2]
        return hashlib.blake2b(np.ascontiguousarray(patch).tobytes(), digest_size=16).digest()

    @classmethod
    def _cache_get(cls, key: bytes) -> tuple[str, float] | None:
        entry = cls._cache.get(key)
        if entry is None:
            return None
        ts, text, conf = entry
        if (time.monotonic() - ts) > cls._OCR_CACHE_TTL_S:
            cls._cache.pop(key, None)
            return None
        cls._cache.move_to_end(key)
        return text, conf

    @classmethod
    def _cache_put(cls, key: bytes, text: str, confidence: float) -> None:
        cls._cache[key] = (time.monotonic(), text, confidence)
        cls._cache.move_to_end(key)
        while len(cls._cache) > cls._OCR_CACHE_MAX:
            cls._cache.popitem(last=False)

    @classmethod
    def clear_cache(cls) -> None:
        """Drop all cached OCR results — for tests and explicit recovery paths."""
        cls._cache.clear()

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.ocr.url
        self._timeout = float(settings.ocr.timeout_seconds)

    def _client_key(self) -> tuple[str, float, int]:
        return (self._base_url, self._timeout, id(asyncio.get_running_loop()))

    async def _http_client(self) -> httpx.AsyncClient:
        key = self._client_key()
        client = self._clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(timeout=self._timeout)
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        client = self._clients.pop(self._client_key(), None)
        if client is not None and not client.is_closed:
            await client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
    async def ocr_regions(
        self,
        image: np.ndarray,
        regions: list[Region],
        *,
        region_ids: list[str] | None = None,
    ) -> list[OCRResult]:
        def _rid(i: int) -> str:
            if region_ids is not None and i < len(region_ids):
                s = str(region_ids[i] or "").strip()
                if s:
                    return s
            return f"r{i}"

        # Pre-fill from the content-addressed cache.  Only regions whose bbox
        # patch has not been OCR'd recently are sent to the backend; identical
        # pixels return the prior result instantly.
        results: list[OCRResult | None] = [None] * len(regions)
        miss_indices: list[int] = []
        miss_keys: list[bytes] = []
        for i, region in enumerate(regions):
            key = self._patch_hash(image, region)
            hit = self._cache_get(key)
            if hit is not None:
                text, conf = hit
                results[i] = OCRResult(region_id=_rid(i), text=text, confidence=conf)
            else:
                miss_indices.append(i)
                miss_keys.append(key)

        if not miss_indices:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                if len(regions) == 1:
                    r = regions[0]
                    extra = f" bbox=({r.x},{r.y},{r.w}x{r.h})"
                else:
                    extra = f" batch={len(regions)}"
                logger.info(
                    "OCR ts=%s ms=0 cached%s — %d hit",
                    ts, extra, len(regions),
                )
            except Exception:
                pass
            return [r for r in results if r is not None]

        _, buf = cv2.imencode(".png", image)
        image_b64 = base64.b64encode(buf.tobytes()).decode()

        region_payloads = [
            {
                "region_id": _rid(idx),
                "x": regions[idx].x,
                "y": regions[idx].y,
                "w": regions[idx].w,
                "h": regions[idx].h,
            }
            for idx in miss_indices
        ]

        client = await self._http_client()
        t0 = time.perf_counter()
        resp = await client.post(
            f"{self._base_url}/ocr",
            json={"image_b64": image_b64, "regions": region_payloads},
        )
        elapsed_ms = 1000.0 * (time.perf_counter() - t0)
        resp.raise_for_status()

        raw_results = resp.json()
        items = raw_results if isinstance(raw_results, list) else []
        # Stitch HTTP responses back into the original request order and warm
        # the cache.  The backend echoes ``region_id`` so we trust the order it
        # returned (matches our miss-list order).
        for slot, item in enumerate(items):
            if not isinstance(item, dict) or slot >= len(miss_indices):
                continue
            idx = miss_indices[slot]
            text = str(item.get("text") or "")
            conf_raw = item.get("confidence")
            try:
                conf = float(conf_raw) if conf_raw is not None else 0.0
            except (TypeError, ValueError):
                conf = 0.0
            results[idx] = OCRResult(region_id=_rid(idx), text=text, confidence=conf)
            self._cache_put(miss_keys[slot], text, conf)

        try:
            max_shown = 6
            parts: list[str] = []
            for item in items[:max_shown]:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("region_id") or "").strip()
                txt = str(item.get("text") or "").strip().replace("\n", " ")
                conf = item.get("confidence")
                conf_f = float(conf) if isinstance(conf, (int, float, str)) and str(conf) else 0.0
                if len(txt) > 80:
                    txt = f"{txt[:77]}..."
                parts.append(
                    f"{_ocr_tty_yellow_name(rid)} conf={conf_f:.3f} text={txt!r}"
                )
            if parts:
                ts = datetime.now().strftime("%H:%M:%S")
                extra = ""
                if len(miss_indices) == 1:
                    r = regions[miss_indices[0]]
                    extra = f" bbox=({r.x},{r.y},{r.w}x{r.h})"
                elif len(items) > 1:
                    extra = f" batch={len(items)}"
                cached = len(regions) - len(miss_indices)
                cache_tag = f" cached={cached}" if cached else ""
                omitted = len(items) - len(parts)
                tail = f" | +{omitted} more" if omitted > 0 else ""
                logger.info(
                    "OCR ts=%s ms=%.0f%s%s %s%s",
                    ts, elapsed_ms, extra, cache_tag, " | ".join(parts), tail,
                )
        except Exception:
            pass

        return [r for r in results if r is not None]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
    async def ocr_region(
        self,
        image: np.ndarray,
        region: Region,
        *,
        region_id: str | None = None,
    ) -> OCRResult:
        rid = (region_id or "").strip() or "r0"
        results = await self.ocr_regions(image, [region], region_ids=[rid])
        return results[0] if results else OCRResult(region_id=rid, text="", confidence=0.0)
