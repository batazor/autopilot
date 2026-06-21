"""Full-image OCR of scattered numbered markers (Dreamscape Memory guides).

Community hidden-object guide images have the numbers ``1``…``N`` drawn on the
scene art. This module finds those digit markers and their positions so the
onboarding workflow can join ``number -> item name`` and pin each item.

This is deliberately separate from :class:`ocr.client.OcrClient.ocr_regions`:
that path crops fixed bboxes, caches on a patch hash, and joins words into lines
(``ocr.client._parse_tesseract_tsv`` discards per-word geometry). Marker
detection wants the opposite — one full-image sparse-text pass that keeps each
word's bbox. Results are best-effort and meant to be reviewed/corrected by an
operator before they are saved.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2  # type: ignore[import-untyped]

from ocr.preprocess import DIGITS_CHAR_WHITELIST

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DigitMarker:
    """One detected numbered marker.

    ``x_pct`` / ``y_pct`` are the marker centre as a percentage of the source
    image (0–100). ``left``/``top``/``width``/``height`` are the raw pixel bbox
    (already divided back by the OCR upscale factor) for overlay/debug.
    """

    value: int
    x_pct: float
    y_pct: float
    conf: float
    left: int
    top: int
    width: int
    height: int


# ── TSV parsing (pure, unit-tested) ──────────────────────────────────────────


@dataclass(frozen=True)
class _Token:
    block: str
    par: str
    line: str
    word: int
    left: float
    top: float
    width: float
    height: float
    conf: float
    text: str


def _parse_tsv_tokens(tsv: str) -> list[_Token]:
    """Read tesseract TSV into word-level tokens, keyed by header name."""
    lines = [ln for ln in tsv.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    header = lines[0].split("\t")
    try:
        idx = {
            name: header.index(name)
            for name in (
                "level",
                "block_num",
                "par_num",
                "line_num",
                "word_num",
                "left",
                "top",
                "width",
                "height",
                "conf",
                "text",
            )
        }
    except ValueError:
        return []

    need = max(idx.values())
    out: list[_Token] = []
    for raw in lines[1:]:
        cols = raw.split("\t")
        if len(cols) <= need:
            continue
        if cols[idx["level"]].strip() != "5":  # word level only
            continue
        text = cols[idx["text"]].strip()
        if not text:
            continue
        try:
            conf = float(cols[idx["conf"]])
            left = float(cols[idx["left"]])
            top = float(cols[idx["top"]])
            width = float(cols[idx["width"]])
            height = float(cols[idx["height"]])
            word = int(cols[idx["word_num"]])
        except ValueError:
            continue
        if conf < 0:  # tesseract emits -1 for structural rows
            continue
        out.append(
            _Token(
                block=cols[idx["block_num"]],
                par=cols[idx["par_num"]],
                line=cols[idx["line_num"]],
                word=word,
                left=left,
                top=top,
                width=width,
                height=height,
                conf=conf,
                text=text,
            )
        )
    return out


def _merge_split_digits(tokens: list[_Token]) -> list[_Token]:
    """Merge adjacent single-digit tokens on the same line into one number.

    With wide inter-digit spacing tesseract sometimes splits e.g. ``"26"`` into
    ``"2"`` then ``"6"``. Merge a run of single-digit tokens that sit on the same
    ``(block, par, line)`` and are horizontally adjacent (gap < 0.6× the left
    token's width). Conservative on purpose — over-merging is worse than
    under-merging because the operator reviews the result.
    """
    by_line: dict[tuple[str, str, str], list[_Token]] = {}
    for t in tokens:
        by_line.setdefault((t.block, t.par, t.line), []).append(t)

    merged: list[_Token] = []
    for group in by_line.values():
        group.sort(key=lambda t: t.left)
        cluster: list[_Token] = []
        for t in group:
            if not cluster:
                cluster = [t]
                continue
            prev = cluster[-1]
            gap = t.left - (prev.left + prev.width)
            adjacent = gap < 0.6 * prev.width
            both_single = len(prev.text) == 1 and len(t.text) == 1
            if both_single and adjacent and prev.text.isdigit() and t.text.isdigit():
                cluster.append(t)
            else:
                merged.append(_collapse_cluster(cluster))
                cluster = [t]
        if cluster:
            merged.append(_collapse_cluster(cluster))
    return merged


def _collapse_cluster(cluster: list[_Token]) -> _Token:
    """Union a run of adjacent single-digit tokens into one multi-digit token."""
    if len(cluster) == 1:
        return cluster[0]
    left = min(t.left for t in cluster)
    top = min(t.top for t in cluster)
    right = max(t.left + t.width for t in cluster)
    bottom = max(t.top + t.height for t in cluster)
    return _Token(
        block=cluster[0].block,
        par=cluster[0].par,
        line=cluster[0].line,
        word=cluster[0].word,
        left=left,
        top=top,
        width=right - left,
        height=bottom - top,
        conf=min(t.conf for t in cluster),
        text="".join(t.text for t in cluster),
    )


def _iou(a: DigitMarker, b: DigitMarker) -> float:
    ax2, ay2 = a.left + a.width, a.top + a.height
    bx2, by2 = b.left + b.width, b.top + b.height
    ix1, iy1 = max(a.left, b.left), max(a.top, b.top)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


def parse_tsv_markers(
    tsv: str,
    img_w: int,
    img_h: int,
    *,
    scale: float = 1.0,
    min_conf: float = 0.0,
    min_value: int = 1,
    max_value: int = 99,
) -> list[DigitMarker]:
    """Parse tesseract TSV (digit whitelist) into deduped :class:`DigitMarker`s.

    ``scale`` is the OCR upscale factor: bbox pixels are divided by it so the
    returned geometry is in original-image space. ``img_w``/``img_h`` are the
    original (un-upscaled) image dimensions used for the percentage centres.
    """
    if img_w <= 0 or img_h <= 0:
        return []
    tokens = _merge_split_digits(_parse_tsv_tokens(tsv))

    markers: list[DigitMarker] = []
    for t in tokens:
        if not t.text.isdigit():
            continue
        value = int(t.text)
        if value < min_value or value > max_value:
            continue
        conf = t.conf / 100.0
        if conf < min_conf:
            continue
        left = t.left / scale
        top = t.top / scale
        width = t.width / scale
        height = t.height / scale
        cx = left + width / 2.0
        cy = top + height / 2.0
        markers.append(
            DigitMarker(
                value=value,
                x_pct=round(cx / img_w * 100.0, 3),
                y_pct=round(cy / img_h * 100.0, 3),
                conf=round(conf, 3),
                left=int(round(left)),
                top=int(round(top)),
                width=int(round(width)),
                height=int(round(height)),
            )
        )

    # Dedup by value (keep highest confidence), then suppress strong overlaps.
    best_by_value: dict[int, DigitMarker] = {}
    for m in markers:
        prev = best_by_value.get(m.value)
        if prev is None or m.conf > prev.conf:
            best_by_value[m.value] = m

    kept: list[DigitMarker] = []
    for m in sorted(best_by_value.values(), key=lambda d: d.conf, reverse=True):
        if any(_iou(m, k) > 0.5 for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda d: d.value)


# ── Tesseract invocation ──────────────────────────────────────────────────────


def _run_tesseract_tsv(
    image_bgr: np.ndarray,
    *,
    tesseract_cmd: str,
    lang: str,
    tessdata_dir: str,
    timeout_s: float,
    psm: int,
    upscale: float,
) -> tuple[str, int, int]:
    """Return ``(tsv, orig_w, orig_h)`` from a sparse-text digit OCR pass.

    Grayscale + cubic upscale only: the markers are small glyphs on busy art, so
    a global threshold (as in ``binary_tile_for_ocr``) would destroy them. The
    upscale is the highest-leverage knob for small-digit recall.
    """
    if image_bgr is None or image_bgr.size == 0:
        return "", 0, 0
    if shutil.which(tesseract_cmd) is None and not Path(tesseract_cmd).exists():
        msg = (
            f"tesseract executable not found: {tesseract_cmd!r}. "
            "Install Tesseract with eng.traineddata or set WOS_TESSERACT_CMD."
        )
        raise RuntimeError(msg)

    orig_h, orig_w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    work = (
        cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        if upscale and upscale != 1.0
        else gray
    )

    ok, buf = cv2.imencode(".png", work)
    if not ok or buf is None:
        msg = "cv2.imencode('.png', crop) failed"
        raise RuntimeError(msg)

    cmd = [
        tesseract_cmd,
        "stdin",
        "stdout",
        "-l",
        lang,
        "--oem",
        "1",
        "--psm",
        str(psm),
        "-c",
        f"tessedit_char_whitelist={DIGITS_CHAR_WHITELIST}",
    ]
    if tessdata_dir:
        cmd.extend(["--tessdata-dir", tessdata_dir])
    cmd.append("tsv")
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        input=buf.tobytes(),
        timeout=timeout_s,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (stderr or stdout or "").strip()
        raise RuntimeError(detail or f"tesseract exited with status {proc.returncode}")
    return stdout, orig_w, orig_h


def detect_digit_markers(
    image_bgr: np.ndarray,
    *,
    tesseract_cmd: str = "tesseract",
    lang: str = "eng",
    tessdata_dir: str = "",
    timeout_s: float = 20.0,
    psm: int = 11,
    min_conf: float = 0.30,
    min_value: int = 1,
    max_value: int = 99,
    upscale: float = 2.0,
) -> list[DigitMarker]:
    """Detect numbered markers in a full BGR image (best-effort, uncached)."""
    tsv, orig_w, orig_h = _run_tesseract_tsv(
        image_bgr,
        tesseract_cmd=tesseract_cmd,
        lang=lang,
        tessdata_dir=tessdata_dir,
        timeout_s=timeout_s,
        psm=psm,
        upscale=upscale,
    )
    return parse_tsv_markers(
        tsv,
        orig_w,
        orig_h,
        scale=upscale,
        min_conf=min_conf,
        min_value=min_value,
        max_value=max_value,
    )
