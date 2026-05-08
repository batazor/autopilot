from __future__ import annotations

import asyncio
import base64
import logging
import time
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


@dataclass(frozen=True)
class OCRResult:
    region_id: str
    text: str
    confidence: float


class OcrClient:
    _clients: ClassVar[dict[tuple[str, float, int], httpx.AsyncClient]] = {}

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
        self, image: np.ndarray, regions: list[Region]
    ) -> list[OCRResult]:
        _, buf = cv2.imencode(".png", image)
        image_b64 = base64.b64encode(buf.tobytes()).decode()

        region_payloads = [
            {
                "region_id": f"r{i}",
                "x": r.x,
                "y": r.y,
                "w": r.w,
                "h": r.h,
            }
            for i, r in enumerate(regions)
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
        # Log short OCR summary at INFO (useful when tuning regions).
        try:
            items = raw_results if isinstance(raw_results, list) else []
            parts: list[str] = []
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("region_id") or "").strip()
                txt = str(item.get("text") or "").strip().replace("\n", " ")
                conf = item.get("confidence")
                conf_f = float(conf) if isinstance(conf, (int, float, str)) and str(conf) else 0.0
                if len(txt) > 80:
                    txt = f"{txt[:77]}..."
                parts.append(f"{rid} conf={conf_f:.3f} text={txt!r}")
            if parts:
                ts = datetime.now().strftime("%H:%M:%S")
                logger.info("OCR ts=%s ms=%.0f %s", ts, elapsed_ms, " | ".join(parts))
        except Exception:
            # Logging must never break OCR flow.
            pass
        return [
            OCRResult(
                region_id=item["region_id"],
                text=item["text"],
                confidence=item["confidence"],
            )
            for item in raw_results
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
    async def ocr_region(self, image: np.ndarray, region: Region) -> OCRResult:
        results = await self.ocr_regions(image, [region])
        return results[0] if results else OCRResult(region_id="r0", text="", confidence=0.0)
