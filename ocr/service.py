from __future__ import annotations

import base64
import logging
import threading
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from paddleocr import PaddleOCR  # type: ignore[import-untyped]
from pydantic import BaseModel

from ocr.paddle_extract import extract_text_confidence, normalize_rec_only_output
from ocr.preprocess import enhance_for_ocr

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
    # Optional per-region preprocessing pipeline. ``"enhance"`` runs the
    # CLAHE + Otsu + 2× upscale path from ``ocr.preprocess.enhance_for_ocr``.
    # Default ``None`` keeps the historical "pass the raw crop to PaddleOCR"
    # behavior — enabling preprocess globally degrades regions whose
    # contrast is already good (white-on-blue UI text, anti-aliased numerals
    # in stat strips), so the trigger is per-region/per-rule on the client.
    preprocess: str | None = None


class OcrRequest(BaseModel):
    image_b64: str
    regions: list[RegionRequest]


class CropRequest(BaseModel):
    """One pre-cropped region as a standalone encoded image.

    Used by ``/ocr_crops`` so the client doesn't ship the full screenshot
    when it only needs a small piece of it. ``image_b64`` is the PNG-encoded
    bytes of the crop itself — no ``x/y/w/h`` since the backend OCRs it
    directly without further slicing.
    """

    region_id: str
    image_b64: str
    preprocess: str | None = None


class OcrCropsRequest(BaseModel):
    regions: list[CropRequest]


class OcrResultItem(BaseModel):
    region_id: str
    text: str
    confidence: float
    # Populated only when the backend failed to OCR this region (e.g. PaddleOCR
    # raised). Clients can tell a real "no text" (None) from a backend error.
    error: str | None = None


def _ocr_one_crop(
    crop: "np.ndarray | None",
    region_id: str,
    *,
    preprocess: str | None = None,
) -> OcrResultItem:
    """Run PaddleOCR on a single pre-sliced crop, return one result item.

    Wraps the per-region try/except + lock used by both ``/ocr`` (where the
    crop is sliced from a full image) and ``/ocr_crops`` (where the client
    sent the crop directly). Bumps ``ocr_failed_regions_total`` on backend
    errors so the metrics surface looks the same for both endpoints.

    ``preprocess`` is an opt-in pipeline tag. Recognised values:

    * ``"enhance"`` — :func:`ocr.preprocess.enhance_for_ocr`
      (CLAHE + Otsu binarization + 2× upscale). Helps faded / low-contrast
      labels; **hurts** high-contrast UI text whose anti-aliased glyphs the
      binarizer collapses, which is why this is opt-in per region.

    Unknown / empty values pass through the raw crop unchanged.
    """
    if crop is None or crop.size == 0:
        return OcrResultItem(region_id=region_id, text="", confidence=0.0)

    work = crop
    pre_tag = (preprocess or "").strip().lower()
    if pre_tag == "enhance":
        try:
            work = enhance_for_ocr(crop)
        except Exception as exc:
            logger.warning(
                "preprocess=%s failed region=%s — falling back to raw crop (%s: %s)",
                pre_tag, region_id, type(exc).__name__, exc,
            )
            work = crop

    paddle = get_paddle()
    try:
        # PaddleOCR/PaddleX inference may not be thread-safe and can crash the
        # process (native "double free" / corruption) under concurrent access.
        # Serialize calls to reduce sporadic 500s and container restarts.
        if pre_tag == "fast_line":
            # ``det=False`` tells paddle to skip the text-detection model and
            # run recognition directly on ``work`` as one single text line.
            # On a 30×150 timer crop the detection model is most of the
            # latency; for ``type: time`` / ``type: integer`` regions where
            # we already know the bbox bounds the line, that work is pure
            # overhead.
            with _paddle_lock:
                ocr_out = paddle.ocr(work, det=False)
            ocr_out = normalize_rec_only_output(ocr_out)
        else:
            with _paddle_lock:
                ocr_out = paddle.ocr(work)
        combined_text, avg_conf = extract_text_confidence(ocr_out)
        return OcrResultItem(
            region_id=region_id,
            text=combined_text,
            confidence=avg_conf,
            error=None,
        )
    except Exception as exc:
        logger.exception(
            "OCR failed region=%s crop_shape=%s",
            region_id,
            getattr(crop, "shape", None),
        )
        _bump_metric("ocr_failed_regions_total")
        return OcrResultItem(
            region_id=region_id,
            text="",
            confidence=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )


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

    # Touch the model up front so warmup latency is attributed correctly.
    get_paddle()
    results: list[OcrResultItem] = []

    for region in req.regions:
        hi, wi = full_image.shape[:2]
        x1 = max(0, min(wi, region.x))
        y1 = max(0, min(hi, region.y))
        x2 = max(0, min(wi, region.x + region.w))
        y2 = max(0, min(hi, region.y + region.h))
        crop = full_image[y1:y2, x1:x2]
        results.append(
            _ocr_one_crop(crop, region.region_id, preprocess=region.preprocess)
        )

    elapsed_ms = 1000.0 * (time.perf_counter() - t0)
    _bump_metric("ocr_requests_total")
    _bump_metric("ocr_regions_total", float(len(req.regions)))
    _bump_metric("ocr_request_ms_sum", elapsed_ms)
    _set_metric("ocr_request_ms_last", elapsed_ms)
    return results


@app.post("/ocr_crops", response_model=list[OcrResultItem])
def ocr_crops_endpoint(req: OcrCropsRequest) -> list[OcrResultItem]:
    """Same OCR pipeline as ``/ocr`` but the client pre-slices the crops.

    Saves bandwidth on the common case where the bot only needs a few
    small bbox patches (countdown timers, stat cells) out of a full
    720x1280 framebuffer. The client picks this endpoint adaptively when
    the total cropped area is much smaller than the full frame; otherwise
    it falls back to ``/ocr`` to amortize PNG encoding overhead.
    """
    t0 = time.perf_counter()
    get_paddle()
    results: list[OcrResultItem] = []

    import cv2  # type: ignore[import-untyped]

    for region in req.regions:
        try:
            img_bytes = base64.b64decode(region.image_b64)
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            crop = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception as exc:
            logger.exception(
                "OCR crop decode failed region=%s", region.region_id
            )
            _bump_metric("ocr_failed_regions_total")
            results.append(
                OcrResultItem(
                    region_id=region.region_id,
                    text="",
                    confidence=0.0,
                    error=f"decode_failed: {type(exc).__name__}: {exc}",
                )
            )
            continue
        results.append(
            _ocr_one_crop(crop, region.region_id, preprocess=region.preprocess)
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
