"""Parse PaddleOCR outputs and join recognition tokens in reading order (top→bottom, left→right)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np

_TOKEN = tuple[tuple[float, float], str, float]


def _polygon_sort_key(box: object | None) -> tuple[float, float]:
    """Sort key: top edge (min y), then left edge (min x)."""
    pts = _polygon_to_xy(box)
    if pts is None or pts.size == 0:
        return (float("inf"), float("inf"))
    return (float(np.min(pts[:, 1])), float(np.min(pts[:, 0])))


def _polygon_to_xy(box: object | None) -> np.ndarray | None:
    if box is None:
        return None
    try:
        arr = np.asarray(box, dtype=float)
        if arr.size < 4:
            return None
        return arr.reshape(-1, 2)
    except (TypeError, ValueError):
        return None


def _append_token(
    items: list[_TOKEN],
    box: object | None,
    text: object,
    confidence: object,
    *,
    fallback_index: int,
) -> None:
    s = str(text or "").strip()
    if not s:
        return
    try:
        conf = float(confidence or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    key = _polygon_sort_key(box)
    if key == (float("inf"), float("inf")):
        key = (0.0, float(fallback_index))
    items.append((key, s, conf))


def _is_nonempty(value: object) -> bool:
    """Truthiness check that tolerates numpy arrays.

    PaddleOCR v3 returns ``dt_polys`` / ``rec_polys`` / ``rec_boxes`` as
    numpy arrays. ``bool(np.array([]))`` raises ``"The truth value of an
    empty array is ambiguous"`` — so plain ``if value:`` / ``a or b`` chains
    break the moment a crop has no detected text.
    """
    if value is None:
        return False
    if isinstance(value, np.ndarray):
        return value.size > 0
    try:
        return bool(value)
    except ValueError:
        return False


def _coerce_list(value: object) -> list[object]:
    """``list(value)`` when ``value`` is iterable, else ``[]`` — never raises."""
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        return list(value) if value.size > 0 else []
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return []


def _tokens_from_dict(ocr_dict: dict[str, Any]) -> list[_TOKEN]:
    items: list[_TOKEN] = []
    texts = _coerce_list(ocr_dict.get("rec_texts"))
    scores = _coerce_list(ocr_dict.get("rec_scores"))
    polys: object = None
    for key in ("dt_polys", "rec_polys", "rec_boxes", "polys", "dt_boxes"):
        candidate = ocr_dict.get(key)
        if _is_nonempty(candidate):
            polys = candidate
            break
    if polys is not None and len(polys) == len(texts):  # type: ignore[arg-type]
        for i, (poly, text, score) in enumerate(zip(polys, texts, scores, strict=False)):
            _append_token(items, poly, text, score, fallback_index=i)
    else:
        for i, (text, score) in enumerate(zip(texts, scores, strict=False)):
            _append_token(items, None, text, score, fallback_index=i)
    return items


def _tokens_from_line_object(line: object, base_index: int) -> list[_TOKEN]:
    """One detection line: ``[polygon, (text, confidence)]`` or similar."""
    items: list[_TOKEN] = []
    if not isinstance(line, (list, tuple)) or len(line) < 2:
        return items
    box = line[0]
    text_conf: Any = line[1]
    if isinstance(text_conf, (list, tuple)) and len(text_conf) >= 2:
        _append_token(items, box, text_conf[0], text_conf[1], fallback_index=base_index)
    elif isinstance(text_conf, dict):
        _append_token(
            items,
            box,
            text_conf.get("text"),
            text_conf.get("confidence", text_conf.get("score", 0.0)),
            fallback_index=base_index,
        )
    return items


def _collect_tokens(ocr_out: object) -> list[_TOKEN]:
    items: list[_TOKEN] = []
    idx = 0

    if not ocr_out:
        return items

    if isinstance(ocr_out, dict):
        return _tokens_from_dict(ocr_out)

    if not isinstance(ocr_out, Iterable) or isinstance(ocr_out, (str, bytes)):
        return items

    for page in ocr_out:
        if isinstance(page, dict):
            items.extend(_tokens_from_dict(page))
            continue

        if not isinstance(page, Iterable) or isinstance(page, (str, bytes)):
            continue

        # Single line: ``[poly, (text, conf)]`` (some callers omit outer batch list).
        if (
            isinstance(page, (list, tuple))
            and len(page) == 2
            and _polygon_to_xy(page[0]) is not None
        ):
            items.extend(_tokens_from_line_object(page, idx))
            idx += 1
            continue

        for line in page:
            got = _tokens_from_line_object(line, idx)
            if got:
                idx += 1
            items.extend(got)

    return items


def normalize_rec_only_output(ocr_out: object) -> object:
    """Reshape ``paddle.ocr(crop, det=False)`` output for ``extract_text_confidence``.

    Detection-off output varies across PaddleOCR versions / wrappers:

    * ``[[("text", conf), ...]]`` — page-wrapped list of rec tuples (most common).
    * ``[("text", conf), ...]`` — flat rec list (some 3.x paths).
    * ``{"rec_texts": [...], "rec_scores": [...]}`` — paddlex-style dict.

    :func:`extract_text_confidence` already handles the dict form natively
    (via ``rec_texts`` / ``rec_scores`` keys); reshape the list variants
    into that dict so the extractor never has to special-case the
    ``det=False`` path. Unknown shapes pass through verbatim — extractor
    falls back to ``""`` / ``0.0`` rather than raising.
    """
    if isinstance(ocr_out, dict):
        return ocr_out
    if not isinstance(ocr_out, list):
        return ocr_out

    items: object = ocr_out
    # Unwrap one layer when paddle returns [[ (t, c), ... ]] for a single page.
    if items and isinstance(items[0], list):
        items = items[0]
    if not isinstance(items, list):
        return ocr_out

    rec_texts: list[object] = []
    rec_scores: list[object] = []
    for entry in items:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            rec_texts.append(entry[0])
            rec_scores.append(entry[1])
    return {"rec_texts": rec_texts, "rec_scores": rec_scores}


def extract_text_confidence(ocr_out: object) -> tuple[str, float]:
    """Normalize PaddleOCR v2/v3 outputs to text + average confidence.

    Recognition boxes are sorted by polygon position (min y, then min x) before joining,
    so a single UI line is read left-to-right even when Paddle returns boxes out of order.
    """
    items = _collect_tokens(ocr_out)
    if not items:
        return "", 0.0

    items.sort(key=lambda it: it[0])
    texts = [t for _, t, _ in items]
    confidences = [c for _, _, c in items]
    return " ".join(texts), sum(confidences) / len(confidences)
