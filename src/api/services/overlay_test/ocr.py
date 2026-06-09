"""Live / uploaded-image region OCR endpoints (Dreamscape badges, OCR tester)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from api.services.click_approval_overlay import (
    load_preview_bytes,
)
from api.services.overlay_test.common import _decode_png_to_bgr, _detect_screen_on_frame, _ordered_unique
from api.services.overlay_test.types import RegionOcrResult, RegionOcrRow, RegionOcrTestResult
from config.paths import repo_root
from dashboard.click_approvals import active_player_state_flat
from dashboard.reference_preview import load_rolling_instance_preview
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import (
    load_area_doc,
)
from layout.types import Region
from ocr.preprocess import parse_digit_count, resolve_preprocess
from services import get_ocr_client

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


def run_region_ocr(
    *,
    client: Any,
    instance_id: str,
    regions: list[str],
    threshold: float | None = None,
) -> RegionOcrResult:
    """OCR the live frame for each named area region and return the text.

    Mirrors the worker's OCR step (:mod:`tasks.dsl_ocr_mixin`): resolve each
    region's bbox from the merged area doc, crop it from the current frame, and
    run it through the same OCR client + preprocess pipeline. Unlike
    ``run_area_region_probe`` (which only returns template-match scores) this
    returns the recognized text — what the badges in the Dreamscape live editor
    need.
    """
    from dashboard.redis_client import get_instance_state

    inst_state = get_instance_state(client, instance_id) or {}
    current_screen = str(inst_state.get("current_screen") or "").strip()
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)

    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)

    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_png_to_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    repo = repo_root()
    area_doc = load_area_doc(repo)
    rows = _ocr_region_rows(
        image_bgr,
        regions,
        area_doc=area_doc,
        state_flat=state_flat,
        current_screen=current_screen,
        threshold=threshold,
        width=width,
        height=height,
    )

    return RegionOcrResult(
        instance_id=instance_id,
        current_screen=current_screen,
        preview={
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
        },
        rows=rows,
    )


def _ocr_region_rows(
    image_bgr: np.ndarray | None,
    region_names: list[str],
    *,
    area_doc: dict[str, Any],
    state_flat: dict[str, Any],
    current_screen: str,
    threshold: float | None,
    width: int,
    height: int,
) -> list[RegionOcrRow]:
    """OCR each named region on ``image_bgr`` — shared by the live + upload paths."""
    ocr_client = get_ocr_client()
    rows: list[RegionOcrRow] = []
    for name in _ordered_unique(region_names):
        if image_bgr is None:
            rows.append(
                RegionOcrRow(
                    region=name,
                    text="",
                    confidence=None,
                    threshold=None,
                    low_confidence=False,
                    status="no_frame",
                    duration_ms=None,
                )
            )
            continue

        pair = screen_region_by_name(
            area_doc, name, state_flat=state_flat, screen_id=current_screen or None
        )
        region_def = pair[1] if pair is not None else None
        bbox = region_def.get("bbox") if isinstance(region_def, dict) else None
        px = py = pw = ph = 0
        if isinstance(bbox, dict):
            try:
                px = int(round(float(bbox["x"]) / 100.0 * width))
                py = int(round(float(bbox["y"]) / 100.0 * height))
                pw = int(round(float(bbox["width"]) / 100.0 * width))
                ph = int(round(float(bbox["height"]) / 100.0 * height))
            except (KeyError, TypeError, ValueError):
                pw = ph = 0
        if not isinstance(bbox, dict) or pw <= 0 or ph <= 0:
            rows.append(
                RegionOcrRow(
                    region=name,
                    text="",
                    confidence=None,
                    threshold=None,
                    low_confidence=False,
                    status="no_region",
                    duration_ms=None,
                )
            )
            continue

        raw_threshold = (
            threshold if threshold is not None else region_def.get("threshold", 0.8)
        )
        safe_threshold = max(0.0, min(1.0, float(raw_threshold)))
        preprocess = resolve_preprocess(
            explicit=region_def.get("preprocess"),
            type_hint=region_def.get("type"),
        )
        digit_count = parse_digit_count(region_def.get("digit_count"))
        try:
            digit_x0 = int(region_def.get("digit_x0", 0) or 0)
        except (TypeError, ValueError):
            digit_x0 = 0

        t0 = time.perf_counter()
        try:
            result = asyncio.run(
                ocr_client.ocr_region(
                    image_bgr,
                    Region(px, py, pw, ph),
                    region_id=name,
                    preprocess=preprocess,
                    digit_count=digit_count,
                    digit_x0=digit_x0,
                )
            )
        except Exception as exc:
            logger.warning("region_ocr: OCR failed for %s: %s", name, exc)
            rows.append(
                RegionOcrRow(
                    region=name,
                    text="",
                    confidence=None,
                    threshold=safe_threshold,
                    low_confidence=False,
                    status="error",
                    duration_ms=round((time.perf_counter() - t0) * 1000.0, 1),
                )
            )
            continue

        duration_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        text = (result.text or "").strip()
        conf = float(result.confidence or 0.0)
        if result.error:
            status = "error"
        elif not text:
            status = "empty"
        else:
            status = "ok"
        rows.append(
            RegionOcrRow(
                region=name,
                text=text,
                confidence=round(conf, 4),
                threshold=safe_threshold,
                low_confidence=bool(text) and conf < safe_threshold,
                status=status,
                duration_ms=duration_ms,
            )
        )
    return rows


def run_region_ocr_test(
    *,
    client: Any,
    instance_id: str,
    image_bytes: bytes,
    regions: list[str],
    threshold: float | None = None,
) -> RegionOcrTestResult:
    """Run screen detection + region OCR on an uploaded image (no persistence).

    Lets the operator test the Dreamscape logic against a custom screenshot
    instead of the live device. Any OpenCV-decodable image (PNG/JPG/…) works.
    """
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)
    image_bgr = _decode_png_to_bgr(image_bytes) if image_bytes else None
    width = height = 0
    if image_bgr is not None:
        height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    repo = repo_root()
    area_doc = load_area_doc(repo)
    detected_screen = ""
    if image_bgr is not None:
        detected_screen, _ms = _detect_screen_on_frame(image_bgr)

    rows = _ocr_region_rows(
        image_bgr,
        regions,
        area_doc=area_doc,
        state_flat=state_flat,
        current_screen=detected_screen,
        threshold=threshold,
        width=width,
        height=height,
    )
    return RegionOcrTestResult(
        instance_id=instance_id,
        detected_screen=detected_screen,
        screen_source="detected" if detected_screen else "none",
        preview={"available": image_bgr is not None, "width": width, "height": height},
        rows=rows,
    )
