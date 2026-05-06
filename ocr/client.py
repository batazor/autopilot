from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

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
    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.ocr.url
        self._timeout = settings.ocr.timeout_seconds

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

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/ocr",
                json={"image_b64": image_b64, "regions": region_payloads},
            )
            resp.raise_for_status()

        raw_results = resp.json()
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
