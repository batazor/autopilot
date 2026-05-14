"""``normalize_rec_only_output`` reshapes paddle's ``det=False`` output.

Paddle's return shape for the recognition-only path isn't stable across
versions, so the OCR service keeps a defensive reshape before handing the
result to :func:`extract_text_confidence`. Locking the input/output mapping
here ensures a paddle upgrade in the OCR container surfaces as a fast-line
test failure, not a silent "text=''" regression for every timer.
"""

from __future__ import annotations

import numpy as np

from ocr.paddle_extract import extract_text_confidence, normalize_rec_only_output


def test_page_wrapped_list_normalizes_to_rec_dict() -> None:
    """Most common paddleocr 2.x output for ``ocr(crop, det=False)``."""
    norm = normalize_rec_only_output([[("hello", 0.9), ("world", 0.8)]])
    assert norm == {
        "rec_texts": ["hello", "world"],
        "rec_scores": [0.9, 0.8],
    }


def test_flat_list_normalizes_to_rec_dict() -> None:
    """Some paddle code paths drop the per-page wrap and return rec items flat."""
    assert normalize_rec_only_output([("hello", 0.9)]) == {
        "rec_texts": ["hello"],
        "rec_scores": [0.9],
    }


def test_dict_form_passes_through_unchanged() -> None:
    """Paddlex-style v3 output is already in the dict form
    ``extract_text_confidence`` handles natively — no reshape needed."""
    payload = {"rec_texts": ["x"], "rec_scores": [0.5]}
    assert normalize_rec_only_output(payload) == payload


def test_unknown_shape_passes_through_verbatim() -> None:
    """Anything we don't recognize falls through. ``extract_text_confidence``
    yields ``""`` / ``0.0`` for shapes it can't parse — better than raising
    a TypeError mid-request and losing every region in the batch."""
    assert normalize_rec_only_output("garbage") == "garbage"
    assert normalize_rec_only_output(None) is None


def test_paddlex_v3_no_text_with_numpy_polys_does_not_raise() -> None:
    """Regression: PaddleOCR v3 returns ``dt_polys`` / ``rec_polys`` /
    ``rec_boxes`` as numpy arrays, and an empty crop (no detected text) yields
    empty arrays. The legacy ``a or b or c`` chain calls ``bool(np.array([]))``
    which raises ``"The truth value of an empty array is ambiguous"``. Guard
    the extractor against that — every per-region OCR call that happens to
    hit a blank crop would otherwise surface as a backend ``error=`` and
    poison the rule's result."""
    empty_dt_polys = np.empty((0, 4, 2), dtype=np.float32)
    empty_rec_polys = np.empty((0, 4, 2), dtype=np.float32)
    empty_rec_boxes = np.empty((0, 4), dtype=np.int32)
    ocr_out = [
        {
            "rec_texts": [],
            "rec_scores": [],
            "dt_polys": empty_dt_polys,
            "rec_polys": empty_rec_polys,
            "rec_boxes": empty_rec_boxes,
        }
    ]
    text, conf = extract_text_confidence(ocr_out)
    assert text == ""
    assert conf == 0.0


def test_paddlex_v3_with_numpy_polys_preserves_token_order() -> None:
    """When PaddleOCR v3 *does* detect text, ``dt_polys`` is a non-empty numpy
    array. The extractor must still pick it up (for reading-order sort) without
    tripping the same truthiness check."""
    dt_polys = np.array(
        [
            [[100.0, 50.0], [200.0, 50.0], [200.0, 80.0], [100.0, 80.0]],
            [[10.0, 10.0], [60.0, 10.0], [60.0, 30.0], [10.0, 30.0]],
        ],
        dtype=np.float32,
    )
    ocr_out = [
        {
            "rec_texts": ["bottom", "top"],
            "rec_scores": [0.9, 0.8],
            "dt_polys": dt_polys,
        }
    ]
    text, conf = extract_text_confidence(ocr_out)
    assert text == "top bottom"
    assert conf == (0.9 + 0.8) / 2


def test_normalized_output_round_trips_through_extractor() -> None:
    """End-to-end: a ``det=False`` paddle result flows through
    ``normalize_rec_only_output`` then ``extract_text_confidence`` to the
    same ``(text, avg_conf)`` shape every other code path expects."""
    paddle_out = [[("01:30:00", 0.92), ("HP", 0.85)]]
    norm = normalize_rec_only_output(paddle_out)
    text, conf = extract_text_confidence(norm)
    assert text == "01:30:00 HP"
    assert conf == (0.92 + 0.85) / 2
