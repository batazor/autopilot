"""Full-frame word-box OCR — locate arbitrary words and their positions.

Sibling of :mod:`ocr.digit_markers`. That module finds numeric markers; this one
keeps every readable *word* and its pixel bbox from a single sparse-text
full-frame pass. The hot :meth:`ocr.client.OcrClient.ocr_regions` path crops
fixed bboxes and joins words into lines (per-word geometry is discarded), so it
cannot answer "*where* on the screen is the word 'Claim'?". This module exists
for exactly that question — a template-free way to find and tap a button by its
label on a screen we don't otherwise recognise (see
``tasks.dsl_exec.claim_on_unknown``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ocr.digit_markers import _parse_tsv_tokens, _run_tesseract_tsv

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WordBox:
    """One OCR'd word and its pixel bbox in original-image space.

    ``left``/``top``/``width``/``height`` are pixels (already divided back by the
    OCR upscale factor). ``cx``/``cy`` are the bbox centre — the natural tap
    target for a button labelled by this word.
    """

    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int

    @property
    def cx(self) -> int:
        return int(round(self.left + self.width / 2.0))

    @property
    def cy(self) -> int:
        return int(round(self.top + self.height / 2.0))


def parse_tsv_word_boxes(
    tsv: str,
    *,
    scale: float = 1.0,
    min_conf: float = 0.0,
) -> list[WordBox]:
    """Parse tesseract TSV into :class:`WordBox`es (pure, unit-tested).

    ``scale`` is the OCR upscale factor: bbox pixels are divided by it so the
    returned geometry is in original-image space.
    """
    boxes: list[WordBox] = []
    for t in _parse_tsv_tokens(tsv):
        conf = t.conf / 100.0
        if conf < min_conf:
            continue
        text = t.text.strip()
        if not text:
            continue
        boxes.append(
            WordBox(
                text=text,
                conf=round(conf, 3),
                left=int(round(t.left / scale)),
                top=int(round(t.top / scale)),
                width=int(round(t.width / scale)),
                height=int(round(t.height / scale)),
            )
        )
    return boxes


def detect_word_boxes(
    image_bgr: np.ndarray,
    *,
    tesseract_cmd: str = "tesseract",
    lang: str = "eng",
    tessdata_dir: str = "",
    timeout_s: float = 20.0,
    psm: int = 11,
    min_conf: float = 0.4,
    upscale: float = 2.0,
) -> list[WordBox]:
    """Detect every readable word + bbox in a full BGR frame (best-effort, uncached).

    Uses PSM 11 (sparse text) with no character whitelist so arbitrary button
    labels are read. Runs tesseract once over a grayscale, cubic-upscaled frame.
    """
    if image_bgr is None or image_bgr.size == 0:
        return []
    tsv, _orig_w, _orig_h = _run_tesseract_tsv(
        image_bgr,
        tesseract_cmd=tesseract_cmd,
        lang=lang,
        tessdata_dir=tessdata_dir,
        timeout_s=timeout_s,
        psm=psm,
        upscale=upscale,
        char_whitelist=None,
    )
    return parse_tsv_word_boxes(tsv, scale=upscale, min_conf=min_conf)
