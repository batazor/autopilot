from __future__ import annotations

import base64
import logging
import threading
from collections.abc import Iterable
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from paddleocr import PaddleOCR  # type: ignore[import-untyped]
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="WOS OCR Service")
_paddle: PaddleOCR | None = None
_paddle_lock = threading.Lock()


def get_paddle() -> PaddleOCR:
    global _paddle  # noqa: PLW0603
    if _paddle is None:
        _paddle = PaddleOCR(use_angle_cls=True, lang="en")
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


def _extract_text_confidence(ocr_out: object) -> tuple[str, float]:
    """Normalize PaddleOCR v2/v3 outputs to text + average confidence."""
    texts: list[str] = []
    confidences: list[float] = []

    def add(text: object, confidence: object = 0.0) -> None:
        s = str(text or "").strip()
        if not s:
            return
        texts.append(s)
        try:
            confidences.append(float(confidence or 0.0))
        except (TypeError, ValueError):
            confidences.append(0.0)

    if not ocr_out:
        return "", 0.0

    if isinstance(ocr_out, dict):
        for text, score in zip(
            ocr_out.get("rec_texts") or [],
            ocr_out.get("rec_scores") or [],
            strict=False,
        ):
            add(text, score)
        return " ".join(texts), sum(confidences) / len(confidences) if confidences else 0.0

    if not isinstance(ocr_out, Iterable) or isinstance(ocr_out, (str, bytes)):
        return "", 0.0

    for page in ocr_out:
        if isinstance(page, dict):
            for text, score in zip(
                page.get("rec_texts") or [],
                page.get("rec_scores") or [],
                strict=False,
            ):
                add(text, score)
            continue

        if not isinstance(page, Iterable) or isinstance(page, (str, bytes)):
            continue
        for line in page:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            text_conf: Any = line[1]
            if isinstance(text_conf, (list, tuple)) and len(text_conf) >= 2:
                add(text_conf[0], text_conf[1])
            elif isinstance(text_conf, dict):
                add(text_conf.get("text"), text_conf.get("confidence", text_conf.get("score", 0.0)))

    return " ".join(texts), sum(confidences) / len(confidences) if confidences else 0.0


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
        h, w = full_image.shape[:2]
        x1 = max(0, min(w, region.x))
        y1 = max(0, min(h, region.y))
        x2 = max(0, min(w, region.x + region.w))
        y2 = max(0, min(h, region.y + region.h))
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
            combined_text, avg_conf = _extract_text_confidence(ocr_out)
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
