from __future__ import annotations

import base64
import logging
import threading
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from paddleocr import PaddleOCR  # type: ignore[import-untyped]
from pydantic import BaseModel

from ocr.paddle_extract import extract_text_confidence

logger = logging.getLogger(__name__)

app = FastAPI(title="WOS OCR Service")
_paddle: PaddleOCR | None = None
_paddle_lock = threading.Lock()


# Lightweight in-process metrics. Aggregate counters so the operator can
# distinguish "no text found" from "OCR backend keeps crashing" without
# scraping every request log.
_metrics_lock = threading.Lock()
_metrics: dict[str, float] = {
    "ocr_requests_total": 0,
    "ocr_regions_total": 0,
    "ocr_failed_regions_total": 0,
    "ocr_request_ms_last": 0.0,
    "ocr_request_ms_sum": 0.0,
}


def _bump_metric(key: str, delta: float = 1.0) -> None:
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + delta


def _set_metric(key: str, value: float) -> None:
    with _metrics_lock:
        _metrics[key] = value


def _snapshot_metrics() -> dict[str, float]:
    with _metrics_lock:
        return dict(_metrics)


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
    # Populated only when the backend failed to OCR this region (e.g. PaddleOCR
    # raised). Clients can tell a real "no text" (None) from a backend error.
    error: str | None = None


@app.on_event("startup")
def _startup() -> None:
    get_paddle()
    logger.info("PaddleOCR model loaded")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict[str, float]:
    """In-process counters for OCR throughput and failures.

    Plain JSON so any HTTP scraper can poll it; not Prometheus exposition
    format yet — small enough that the operator inspects it manually.
    """
    return _snapshot_metrics()


@app.post("/ocr", response_model=list[OcrResultItem])
def ocr_endpoint(req: OcrRequest) -> list[OcrResultItem]:
    t0 = time.perf_counter()
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
        error: str | None = None
        try:
            # PaddleOCR/PaddleX inference may not be thread-safe and can crash the
            # process (native "double free" / corruption) under concurrent access.
            # Serialize calls to reduce sporadic 500s and container restarts.
            with _paddle_lock:
                ocr_out = paddle.ocr(crop)
            combined_text, avg_conf = extract_text_confidence(ocr_out)
        except Exception as exc:
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
            error = f"{type(exc).__name__}: {exc}"
            _bump_metric("ocr_failed_regions_total")
        results.append(
            OcrResultItem(
                region_id=region.region_id,
                text=combined_text,
                confidence=avg_conf,
                error=error,
            )
        )

    elapsed_ms = 1000.0 * (time.perf_counter() - t0)
    _bump_metric("ocr_requests_total")
    _bump_metric("ocr_regions_total", float(len(req.regions)))
    _bump_metric("ocr_request_ms_sum", elapsed_ms)
    _set_metric("ocr_request_ms_last", elapsed_ms)
    return results


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
