"""``cv2.ml.KNearest`` digit strip classifier (chief-profile player id)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

DIGIT_CELL_W = 20
DIGIT_CELL_H = 32
DEFAULT_K = 3
DEFAULT_X0 = 0
MIN_COUNT_GLYPH_W = 6  # projection peaks counted when inferring digit count
MIN_GLYPH_W = 6  # min width for a run to become a digit bounding box
# 6, not 8: the slim "1" in chief_profile player.power ("17,492") is ~7 px wide
# in the labelled reference and was otherwise dropped, forcing the segmenter to
# fall back to equal-width cells over the whole strip.
MAX_GLYPH_W = 24
MIN_DIGITS = 8
MAX_DIGITS = 11
DEFAULT_CELL_W = 13
# Typical player-id length; only used when ``parse_digit_count`` gets an invalid value.
DEFAULT_DIGIT_COUNT = 9


@dataclass(frozen=True)
class DigitPrediction:
    text: str
    confidence: float
    per_digit_conf: tuple[float, ...]


@dataclass(frozen=True)
class DigitTemplatePrediction:
    text: str
    confidence: float
    per_digit_conf: tuple[float, ...]


@dataclass(frozen=True)
class CompactNumberPrediction:
    text: str
    value_text: str
    confidence: float
    per_glyph_conf: tuple[float, ...]


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def binarize_for_digits(gray: np.ndarray) -> np.ndarray:
    work = gray.copy()
    _, bw = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(bw.mean()) > 127.0:
        bw = 255 - bw
    return bw


def glyph_to_feature(
    gray: np.ndarray,
    *,
    cell_w: int = DIGIT_CELL_W,
    cell_h: int = DIGIT_CELL_H,
) -> np.ndarray:
    if gray.size == 0:
        msg = "empty glyph patch"
        raise ValueError(msg)
    resized = cv2.resize(gray, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
    return (resized.astype(np.float32).ravel() / 255.0)


def glyph_to_mask(
    gray: np.ndarray,
    *,
    cell_w: int = DIGIT_CELL_W,
    cell_h: int = DIGIT_CELL_H,
) -> np.ndarray:
    """Normalize a glyph to a binary mask for template/hash matching."""
    feat = glyph_to_feature(gray, cell_w=cell_w, cell_h=cell_h).reshape(cell_h, cell_w)
    return (binarize_for_digits((feat * 255.0).astype(np.uint8)) > 0).astype(np.uint8)


def augment_glyph(gray: np.ndarray, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    out: list[np.ndarray] = [gray]
    h, w = gray.shape[:2]
    for scale in (0.88, 1.12):
        nh = max(4, int(h * scale))
        nw = max(2, int(w * scale))
        scaled = cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros_like(gray)
        y0 = max(0, (h - nh) // 2)
        x0 = max(0, (w - nw) // 2)
        y1 = min(h, y0 + nh)
        x1 = min(w, x0 + nw)
        canvas[y0:y1, x0:x1] = scaled[: y1 - y0, : x1 - x0]
        out.append(canvas)
    noisy = gray.astype(np.int16)
    noisy += rng.integers(-18, 19, size=gray.shape, dtype=np.int16)
    out.append(np.clip(noisy, 0, 255).astype(np.uint8))
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    out.append(blurred)
    return out


def parse_digit_count(raw: object) -> int | None:
    """``None`` / ``auto`` → projection auto-count; positive int → fixed width."""
    if raw is None:
        return None
    if isinstance(raw, str):
        tag = raw.strip().lower()
        if tag in ("", "auto", "none"):
            return None
        try:
            n = int(tag)
        except ValueError:
            return None
        return n if n > 0 else None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _projection_runs(gray: np.ndarray, x0: int = 0) -> list[tuple[int, int]]:
    work = gray[:, x0:]
    if work.size == 0:
        return []
    bw = binarize_for_digits(work)
    proj = (bw > 0).sum(axis=0)
    runs: list[tuple[int, int]] = []
    in_run = False
    start = 0
    for i, v in enumerate(proj):
        if v > 0 and not in_run:
            start = i
            in_run = True
        elif v == 0 and in_run:
            runs.append((start, i))
            in_run = False
    if in_run:
        runs.append((start, len(proj)))
    return runs


def _median_cell_width(
    narrow: list[tuple[int, int]],
    *,
    default: int = DEFAULT_CELL_W,
) -> int:
    if not narrow:
        return default
    cell = int(round(sum(e - s for s, e in narrow) / len(narrow)))
    return max(MIN_GLYPH_W, min(MAX_GLYPH_W, cell))


def estimate_digit_count(
    gray: np.ndarray,
    *,
    x0: int = 0,
    runs: list[tuple[int, int]] | None = None,
) -> int:
    """Infer digit count from vertical-projection runs (narrow + wide splits)."""
    proj_runs = runs if runs is not None else _projection_runs(gray, x0)
    digitish = [
        (s, e) for s, e in proj_runs if MIN_COUNT_GLYPH_W <= (e - s) <= MAX_GLYPH_W
    ]
    wide = [(s, e) for s, e in proj_runs if (e - s) > MAX_GLYPH_W]
    narrow = [(s, e) for s, e in digitish if (e - s) >= MIN_GLYPH_W]
    cell = _median_cell_width(narrow or digitish)
    count = len(digitish)
    for s, e in wide:
        count += max(1, int(round((e - s) / cell)))
    if MIN_DIGITS <= count <= MAX_DIGITS:
        return count

    work = gray[:, x0:]
    if work.size == 0:
        return MIN_DIGITS
    bw = binarize_for_digits(work)
    cols = (bw > 0).any(axis=0)
    idx = np.where(cols)[0]
    if len(idx) == 0:
        return max(MIN_DIGITS, min(MAX_DIGITS, count))
    span = int(idx[-1]) - int(idx[0]) + 1
    est = max(1, int(round(span / cell)))
    return max(MIN_DIGITS, min(MAX_DIGITS, est))


def _equal_width_boxes(gray: np.ndarray, *, count: int, x0: int = 0) -> list[tuple[int, int]]:
    work = gray[:, x0:]
    w = work.shape[1]
    step = w / float(count)
    return [
        (x0 + int(i * step), x0 + int((i + 1) * step)) for i in range(count)
    ]


def segment_digit_boxes(
    gray: np.ndarray,
    *,
    expected_count: int | None = None,
    x0: int = 0,
) -> list[tuple[int, int]]:
    """Segment a digit strip.

    ``expected_count=None`` (auto): count digits via projection, use narrow
    boxes when the count matches, otherwise equal-width cells over the strip.
    """
    if expected_count is not None and expected_count < 1:
        msg = f"expected_count must be positive, got {expected_count}"
        raise ValueError(msg)

    runs = _projection_runs(gray, x0)
    narrow = [
        (s, e)
        for s, e in runs
        if MIN_GLYPH_W <= (e - s) <= MAX_GLYPH_W
    ]
    count = (
        expected_count
        if expected_count is not None
        else estimate_digit_count(gray, x0=x0, runs=runs)
    )

    if len(narrow) == count:
        return [(s + x0, e + x0) for s, e in narrow]
    if expected_count is not None and len(narrow) > expected_count:
        narrow = narrow[-expected_count:]
        return [(s + x0, e + x0) for s, e in narrow]

    return _equal_width_boxes(gray, count=count, x0=x0)


def _split_projection_run(
    proj: np.ndarray,
    start: int,
    end: int,
    *,
    target_w: int = 15,
) -> list[tuple[int, int]]:
    width = end - start
    count = max(1, int(round(width / float(target_w))))
    if count <= 1:
        return [(start, end)]

    cuts: list[int] = []
    for i in range(1, count):
        target = start + int(round(width * i / float(count)))
        search = max(2, int(round(target_w * 0.45)))
        lo = max(start + 2, target - search)
        hi = min(end - 2, target + search)
        if lo >= hi:
            cuts.append(target)
            continue
        local = proj[lo:hi]
        cuts.append(lo + int(np.argmin(local)))

    out: list[tuple[int, int]] = []
    prev = start
    for cut in sorted(set(cuts)):
        if cut - prev >= 2:
            out.append((prev, cut))
        prev = cut
    if end - prev >= 2:
        out.append((prev, end))
    return out or [(start, end)]


def segment_compact_glyph_boxes(
    gray: np.ndarray,
    *,
    x0: int = 0,
) -> list[tuple[int, int]]:
    """Segment compact stat text: digits plus punctuation/suffix glyphs."""
    work = gray[:, x0:]
    if work.size == 0:
        return []
    bw = binarize_for_digits(work)
    proj = (bw > 0).sum(axis=0)
    boxes: list[tuple[int, int]] = []
    for start, end in _projection_runs(work, x0=0):
        width = end - start
        if width < 2:
            continue
        if width < MIN_GLYPH_W:
            boxes.append((x0 + start, x0 + end))
            continue
        if width >= 22:
            boxes.extend((x0 + s, x0 + e) for s, e in _split_projection_run(proj, start, end))
            continue
        boxes.append((x0 + start, x0 + end))
    if len(boxes) < 2:
        return boxes

    widths = [e - s for s, e in boxes if e > s]
    median_w = float(np.median(widths)) if widths else float(DEFAULT_CELL_W)
    large_gap = max(10, int(round(median_w * 1.25)))
    trimmed = [boxes[0]]
    for prev, box in pairwise(boxes):
        gap = box[0] - prev[1]
        if gap >= large_gap:
            break
        trimmed.append(box)
    return trimmed


def parse_compact_number_text(text: str) -> int | None:
    raw = str(text or "").strip().upper().replace(",", "")
    raw = raw.replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMB])?", raw)
    if not match:
        digits = re.sub(r"\D+", "", raw)
        return int(digits) if digits else None
    value = float(match.group(1))
    suffix = match.group(2) or ""
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return int(round(value * multiplier))


def extract_labeled_glyphs(
    crop_bgr: np.ndarray,
    label: str,
    *,
    x0: int = 0,
) -> list[tuple[str, np.ndarray]]:
    gray = _to_gray(crop_bgr)
    boxes = segment_digit_boxes(gray, expected_count=len(label), x0=x0)
    if len(boxes) != len(label):
        msg = f"segmentation produced {len(boxes)} boxes for {len(label)} digits"
        raise ValueError(msg)
    glyphs: list[tuple[str, np.ndarray]] = []
    for ch, (x1, x2) in zip(label, boxes, strict=True):
        x2 = max(x1 + 1, x2)
        glyphs.append((ch, gray[:, x1:x2]))
    return glyphs


def extract_labeled_compact_glyphs(
    crop_bgr: np.ndarray,
    label: str,
    *,
    x0: int = 0,
) -> list[tuple[str, np.ndarray]]:
    gray = _to_gray(crop_bgr)
    boxes = segment_compact_glyph_boxes(gray, x0=x0)
    if len(boxes) != len(label):
        msg = f"compact segmentation produced {len(boxes)} boxes for {len(label)} glyphs"
        raise ValueError(msg)
    glyphs: list[tuple[str, np.ndarray]] = []
    for ch, (x1, x2) in zip(label, boxes, strict=True):
        x2 = max(x1 + 1, x2)
        glyphs.append((ch, gray[:, x1:x2]))
    return glyphs


def render_synthetic_digit(
    ch: str,
    *,
    cell_h: int = 28,
    font_scale: float = 0.55,
) -> np.ndarray:
    canvas = np.full((cell_h, DIGIT_CELL_W + 8), 28, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), _baseline = cv2.getTextSize(ch, font, font_scale, 2)
    x = max(0, (canvas.shape[1] - tw) // 2)
    y = max(th + 2, (canvas.shape[0] + th) // 2)
    cv2.putText(canvas, ch, (x, y), font, font_scale, 220, 2, cv2.LINE_AA)
    return canvas


class DigitClassifier:
    """Thin wrapper around ``cv2.ml.KNearest``."""

    def __init__(self, model: cv2.ml.KNearest, *, k: int = DEFAULT_K) -> None:
        self._model = model
        self._k = k

    @classmethod
    def train_from_samples(
        cls,
        features: np.ndarray,
        labels: np.ndarray,
        *,
        k: int = DEFAULT_K,
    ) -> DigitClassifier:
        if features.ndim != 2:
            msg = "features must be N×D"
            raise ValueError(msg)
        if labels.ndim != 1 or labels.shape[0] != features.shape[0]:
            msg = "labels must be length N"
            raise ValueError(msg)
        knn = cv2.ml.KNearest_create()
        knn.setDefaultK(int(k))
        knn.train(features.astype(np.float32), cv2.ml.ROW_SAMPLE, labels.astype(np.float32))
        return cls(knn, k=k)

    @classmethod
    def load(cls, path: Path) -> DigitClassifier:
        knn = cv2.ml.KNearest_load(str(path))
        if knn.empty():
            msg = f"failed to load kNN model: {path}"
            raise ValueError(msg)
        return cls(knn, k=int(knn.getDefaultK()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._model.save(str(path))
        if not path.is_file():
            msg = f"cv2.ml.KNearest.save did not create: {path}"
            raise RuntimeError(msg)

    def predict_feature(self, feature: np.ndarray) -> tuple[int, float]:
        sample = feature.astype(np.float32).reshape(1, -1)
        _retval, results, _neighbours, dist = self._model.findNearest(sample, self._k)
        pred = int(results[0, 0])
        d = float(dist[0, 0]) if dist is not None else 0.0
        conf = 1.0 / (1.0 + d)
        return pred, conf

    def predict_glyphs(self, glyphs: list[np.ndarray]) -> DigitPrediction:
        chars: list[str] = []
        confs: list[float] = []
        for g in glyphs:
            digit, conf = self.predict_feature(glyph_to_feature(g))
            chars.append(str(digit))
            confs.append(conf)
        text = "".join(chars)
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        return DigitPrediction(
            text=text,
            confidence=mean_conf,
            per_digit_conf=tuple(confs),
        )

    def predict_strip(
        self,
        crop_bgr: np.ndarray,
        *,
        digit_count: int | None = None,
        x0: int = DEFAULT_X0,
    ) -> DigitPrediction:
        gray = _to_gray(crop_bgr)
        boxes = segment_digit_boxes(gray, expected_count=digit_count, x0=x0)
        glyphs = [gray[:, max(x1, 0) : max(x2, x1 + 1)] for x1, x2 in boxes]
        return self.predict_glyphs(glyphs)

    def predict_compact_number(
        self,
        crop_bgr: np.ndarray,
        *,
        x0: int = DEFAULT_X0,
    ) -> CompactNumberPrediction:
        gray = _to_gray(crop_bgr)
        boxes = segment_compact_glyph_boxes(gray, x0=x0)
        glyphs = [gray[:, max(x1, 0) : max(x2, x1 + 1)] for x1, x2 in boxes]
        pred = self.predict_glyphs(glyphs)
        value = parse_compact_number_text(pred.text)
        return CompactNumberPrediction(
            text=pred.text,
            value_text="" if value is None else str(value),
            confidence=pred.confidence,
            per_glyph_conf=pred.per_digit_conf,
        )


class TemplateDigitClassifier:
    """Nearest-template classifier over normalized binary digit glyph masks."""

    def __init__(self, templates: list[tuple[str, np.ndarray]]) -> None:
        if not templates:
            msg = "templates must not be empty"
            raise ValueError(msg)
        self._labels = tuple(str(digit) for digit, _mask in templates)
        self._masks = np.stack([mask.astype(np.uint8) for _digit, mask in templates])
        self._pixels = float(self._masks.shape[1] * self._masks.shape[2])

    @classmethod
    def from_dataset(cls, dataset_root: Path) -> TemplateDigitClassifier:
        rows = load_dataset_manifest(dataset_root)
        if not rows:
            msg = f"no digit samples under {dataset_root}"
            raise ValueError(msg)
        templates: list[tuple[str, np.ndarray]] = []
        for ch, path in rows:
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            templates.append((ch, glyph_to_mask(img)))
        return cls(templates)

    def predict_glyph(self, gray: np.ndarray) -> tuple[str, float]:
        query = glyph_to_mask(gray)
        diffs = np.count_nonzero(self._masks != query, axis=(1, 2))
        best_idx = int(np.argmin(diffs))
        dist = float(diffs[best_idx]) / self._pixels
        return self._labels[best_idx], max(0.0, 1.0 - dist)

    def predict_glyphs(self, glyphs: list[np.ndarray]) -> DigitTemplatePrediction:
        chars: list[str] = []
        confs: list[float] = []
        for glyph in glyphs:
            digit, conf = self.predict_glyph(glyph)
            chars.append(digit)
            confs.append(conf)
        return DigitTemplatePrediction(
            text="".join(chars),
            confidence=sum(confs) / len(confs) if confs else 0.0,
            per_digit_conf=tuple(confs),
        )

    def predict_strip(
        self,
        crop_bgr: np.ndarray,
        *,
        digit_count: int | None = None,
        x0: int = DEFAULT_X0,
    ) -> DigitTemplatePrediction:
        gray = _to_gray(crop_bgr)
        boxes = segment_digit_boxes(gray, expected_count=digit_count, x0=x0)
        glyphs = [gray[:, max(x1, 0) : max(x2, x1 + 1)] for x1, x2 in boxes]
        return self.predict_glyphs(glyphs)

    def predict_compact_number(
        self,
        crop_bgr: np.ndarray,
        *,
        x0: int = DEFAULT_X0,
    ) -> CompactNumberPrediction:
        gray = _to_gray(crop_bgr)
        boxes = segment_compact_glyph_boxes(gray, x0=x0)
        glyphs = [gray[:, max(x1, 0) : max(x2, x1 + 1)] for x1, x2 in boxes]
        pred = self.predict_glyphs(glyphs)
        value = parse_compact_number_text(pred.text)
        return CompactNumberPrediction(
            text=pred.text,
            value_text="" if value is None else str(value),
            confidence=pred.confidence,
            per_glyph_conf=pred.per_digit_conf,
        )


def load_dataset_manifest(dataset_root: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    for d in range(10):
        folder = dataset_root / str(d)
        if not folder.is_dir():
            continue
        rows.extend((str(d), png) for png in sorted(folder.glob("*.png")))
    return rows


def build_training_matrices(
    dataset_root: Path,
) -> tuple[np.ndarray, np.ndarray]:
    rows = load_dataset_manifest(dataset_root)
    if not rows:
        msg = f"no digit samples under {dataset_root}"
        raise ValueError(msg)
    feats: list[np.ndarray] = []
    labels: list[int] = []
    for ch, path in rows:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        feats.append(glyph_to_feature(img))
        labels.append(int(ch))
    if not feats:
        msg = "no readable digit images"
        raise ValueError(msg)
    return np.vstack(feats), np.array(labels, dtype=np.float32)


def save_dataset_meta(dataset_root: Path, meta: dict[str, Any]) -> None:
    (dataset_root / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
