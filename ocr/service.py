from __future__ import annotations

import base64
import io
import logging

import numpy as np
from fastapi import FastAPI, HTTPException
from paddleocr import PaddleOCR  # type: ignore[import-untyped]
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="WOS OCR Service")
_paddle: PaddleOCR | None = None


def get_paddle() -> PaddleOCR:
    global _paddle  # noqa: PLW0603
    if _paddle is None:
        _paddle = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    return _paddle


class RegionRequest(BaseModel):
    region_id: str
    x: int
    y: int
    w: int
    h: int


class OcrRequest(BaseModel):
    image_b64: str
    regions: list[RegionRequest]


class OcrResultItem(BaseModel):
    region_id: str
    text: str
    confidence: float


@app.on_event("startup")
def _startup() -> None:
    get_paddle()
    logger.info("PaddleOCR model loaded")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ocr", response_model=list[OcrResultItem])
def ocr_endpoint(req: OcrRequest) -> list[OcrResultItem]:
    try:
        img_bytes = base64.b64decode(req.image_b64)
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        import cv2  # type: ignore[import-untyped]

        full_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    paddle = get_paddle()
    results: list[OcrResultItem] = []

    for region in req.regions:
        crop = full_image[region.y : region.y + region.h, region.x : region.x + region.w]
        if crop.size == 0:
            results.append(OcrResultItem(region_id=region.region_id, text="", confidence=0.0))
            continue

        ocr_out = paddle.ocr(crop, cls=True)
        if not ocr_out or not ocr_out[0]:
            results.append(OcrResultItem(region_id=region.region_id, text="", confidence=0.0))
            continue

        texts: list[str] = []
        confidences: list[float] = []
        for line in ocr_out[0]:
            text_conf = line[1]
            texts.append(text_conf[0])
            confidences.append(float(text_conf[1]))

        combined_text = " ".join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        results.append(
            OcrResultItem(region_id=region.region_id, text=combined_text, confidence=avg_conf)
        )

    return results


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
