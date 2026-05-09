from __future__ import annotations

import base64
import logging
import threading

import numpy as np
from fastapi import FastAPI, HTTPException
from paddleocr import PaddleOCR  # type: ignore[import-untyped]
from pydantic import BaseModel

from ocr.paddle_extract import extract_text_confidence

logger = logging.getLogger(__name__)

app = FastAPI(title="WOS OCR Service")
_paddle: PaddleOCR | None = None
_paddle_lock = threading.Lock()


def get_paddle() -> PaddleOCR:
    global _paddle  # noqa: PLW0603
    if _paddle is None:
        # use_doc_orientation_classify and use_doc_unwarping are PaddleOCR v3 document-level
        # preprocessors designed for full pages. On small UI crops they misclassify rotation
        # (e.g. detect 180° flip on a 33px-tall strip) and corrupt OCR output.
        _paddle = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
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
    if full_image is None:
        raise HTTPException(status_code=400, detail="Invalid image: decode failed")

    paddle = get_paddle()
    results: list[OcrResultItem] = []

    for region in req.regions:
        hi, wi = full_image.shape[:2]
        x1 = max(0, min(wi, region.x))
        y1 = max(0, min(hi, region.y))
        x2 = max(0, min(wi, region.x + region.w))
        y2 = max(0, min(hi, region.y + region.h))
        crop = full_image[y1:y2, x1:x2]
        if crop.size == 0:
            results.append(OcrResultItem(region_id=region.region_id, text="", confidence=0.0))
            continue

        # Newer PaddleOCR releases do not accept `cls=` at call time.
        # Angle classification is controlled by `use_angle_cls` at init.
        try:
            # PaddleOCR/PaddleX inference may not be thread-safe and can crash the
            # process (native "double free" / corruption) under concurrent access.
            # Serialize calls to reduce sporadic 500s and container restarts.
            with _paddle_lock:
                ocr_out = paddle.ocr(crop)
            combined_text, avg_conf = extract_text_confidence(ocr_out)
        except Exception:
            logger.exception(
                "OCR failed region=%s crop_xywh=(%d,%d,%d,%d) crop_shape=%s",
                region.region_id,
                x1,
                y1,
                x2 - x1,
                y2 - y1,
                getattr(crop, "shape", None),
            )
            combined_text, avg_conf = "", 0.0
        results.append(
            OcrResultItem(region_id=region.region_id, text=combined_text, confidence=avg_conf)
        )

    return results


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
