"""Parse the event-detail popup: full name + exact start/end datetimes.

Tapping an event bar on the calendar opens a white card whose first text line is
the event name and second is the range ``YYYY-MM-DD HH:MM - YYYY-MM-DD HH:MM``.
That popup is the reliable read — far more robust than the truncated bar labels
or x→day-column geometry (see [[calendar-reading-approach]]).

The card's vertical position follows the tapped bar, so nothing is read at fixed
coordinates: :func:`find_card_bbox` locates the white card, then text bands are
detected *inside* it (excluding the left icon column, which would otherwise
split the title row). The date line is OCR'd with the title preprocess — which
renders the ``-``/``:`` separators as spaces, leaving ten clean number groups —
then a digit-confusion fixup repairs the common ``8→B`` / ``0→O`` misreads
before the groups are reassembled into two timestamps.

``end`` uses ``HH:MM`` up to ``24:00`` (the game's exclusive end-of-day marker),
which rolls into 00:00 of the next day via timedelta arithmetic.

Pure except for ``ocr`` — a ``callable(crop, preprocess) -> (text, confidence)``
injected by the caller (the runtime passes ``OcrClient._run_tesseract``), so the
detection/parse logic is unit-testable against saved frames.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import cv2  # type: ignore[import-untyped]
import numpy as np

OcrFn = Callable[..., tuple[str, float]]  # (crop, *, preprocess) -> (text, conf)

# Card detection: a large near-white rounded rect below the tab strip.
_CARD_WHITE_MIN = 222
_CARD_MIN_W = 330
_CARD_MIN_H = 180
# The popup opens next to the tapped bar, so a top-row event's card lands high
# on screen (just under the title bar). Only the title bar (~0-75) is off-limits.
_CARD_MIN_Y = 80

# Text-band detection inside the card.
_DARK_MAX = 140          # a "dark text" pixel (navy glyphs on the light card)
_ROW_DARK_FRAC = 0.03    # row counts as text if this fraction is dark
_MIN_BAND_H = 10
_ICON_COL_PX = 95        # title text sits right of the event icon
_PAD = 3

# A letter in the date line is always a misread digit (the line has none) — map
# the common Tesseract confusions back before extracting number groups.
_DIGIT_FIX = str.maketrans(
    {"B": "8", "O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1",
     "|": "1", "i": "1", "S": "5", "s": "5", "Z": "2", "z": "2", "G": "6",
     "g": "9", "T": "7", "A": "4"}
)


@dataclass(frozen=True, slots=True)
class PopupEvent:
    """One event read off its detail popup."""

    name: str
    starts_at: datetime
    ends_at: datetime


# A reward badge / icon edge to the left of the title sometimes bleeds one stray
# glyph into the OCR ("x Foundry Battle", "1 Vault of Enigma", "2 Fortress
# Battles"). Strip a single leading isolated char/digit when a real (letter-led)
# name follows. Position-independent, so it holds wherever the popup lands.
_LEADING_NOISE_RE = re.compile(r"^[A-Za-z0-9]\s+(?=[A-Za-z])")


def clean_event_name(raw: str) -> str:
    """Normalize an OCR'd event name: collapse whitespace, drop leading noise."""
    text = " ".join(raw.split())
    return _LEADING_NOISE_RE.sub("", text).strip()


# Event-bar candidate detection. The day-strip/clock header is sticky at the top
# and the hint is a sticky footer, so the scrollable event rows always occupy a
# fixed vertical band — detect colored bars there and return a tap point on each.
# We do NOT try to tell events from section dividers here: a tap on a divider (or
# empty grid) opens no parseable popup, so :func:`parse_popup` is the validator.
_CONTENT_Y0 = 315
_CONTENT_Y1 = 1175
_BAR_SAT_MIN = 90        # saturated, bright pixels = a colored bar (vs pale grid)
_BAR_VAL_MIN = 110
_BAR_MIN_ROW_FRAC = 0.04
_BAR_MIN_H = 22


def detect_event_bars(image_bgr: np.ndarray) -> list[tuple[int, int]]:
    """Candidate tap points (x, y) — one per colored bar in the event area.

    Tap the horizontal *center* of each bar's visible span (not its left end):
    bars that begin before the window extend off the left edge, where a tap lands
    on the rounded cap or a section divider and opens nothing. Over-inclusive on
    purpose (dividers may slip in); the caller taps each and keeps only those
    that open a valid popup. Ordered top-to-bottom.
    """
    if image_bgr is None or image_bgr.ndim != 3:
        return []
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    colored = ((hsv[:, :, 1] > _BAR_SAT_MIN) & (hsv[:, :, 2] > _BAR_VAL_MIN)).astype(np.uint8)
    h, _w = colored.shape
    y1 = min(_CONTENT_Y1, h)
    row_on = colored.mean(axis=1) > _BAR_MIN_ROW_FRAC
    points: list[tuple[int, int]] = []
    y = _CONTENT_Y0
    while y < y1:
        if not row_on[y]:
            y += 1
            continue
        y_end = y
        while y_end < y1 and row_on[y_end]:
            y_end += 1
        if y_end - y >= _BAR_MIN_H:
            band = colored[y:y_end]
            xs = np.where(band.sum(axis=0) > (y_end - y) * 0.4)[0]
            if len(xs):
                cx = (int(xs.min()) + int(xs.max())) // 2
                points.append((cx, (y + y_end) // 2))
        y = y_end + 1
    return points


def find_card_bbox(image_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box ``(x, y, w, h)`` of the popup's white card, or ``None``.

    Picks the largest card-sized white component below the tab strip, so it
    works wherever the popup lands (its position tracks the tapped bar).
    """
    if image_bgr is None or image_bgr.ndim != 3:
        return None
    white = (image_bgr.min(axis=2) > _CARD_WHITE_MIN).astype(np.uint8)
    count, _labels, stats, _c = cv2.connectedComponentsWithStats(white, 8)
    best: tuple[int, int, int, int] | None = None
    best_area = 0
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w >= _CARD_MIN_W and h >= _CARD_MIN_H and y >= _CARD_MIN_Y and area > best_area:
            best, best_area = (x, y, w, h), area
    return best


_GO_MIN_W = 120          # the Go button is a wide pill (reward icons are ~80px squares)
_GO_MAX_W = 380
_GO_MIN_H = 30
_GO_MAX_H = 95


def find_go_button(image_bgr: np.ndarray, card_bbox: tuple[int, int, int, int] | None = None) -> tuple[int, int] | None:
    """Center ``(x, y)`` of the blue **Go** button in a popup, or ``None``.

    The Go button is the wide blue pill in the lower part of the card — tapping
    it navigates to the event. Returns ``None`` when no card / no button is
    visible (e.g. the popup scrolled it off-screen), so the caller can fail the
    route cleanly.
    """
    if image_bgr is None or image_bgr.ndim != 3:
        return None
    card = card_bbox or find_card_bbox(image_bgr)
    if card is None:
        return None
    cx, cy, cw, ch = card
    b = image_bgr[:, :, 0].astype(int)
    g = image_bgr[:, :, 1].astype(int)
    r = image_bgr[:, :, 2].astype(int)
    blue = ((b > 150) & (b > r + 50) & (g > 80) & (g < 210)).astype(np.uint8)
    # restrict to the lower half of the card (the action row sits at the bottom)
    blue[: cy + ch // 2] = 0
    blue[cy + ch :] = 0
    blue[:, :cx] = 0
    blue[:, cx + cw :] = 0
    count, _lab, stats, centroids = cv2.connectedComponentsWithStats(blue, 8)
    best: tuple[int, int] | None = None
    best_area = 0
    for i in range(1, count):
        _x, _y, w, h, area = (int(v) for v in stats[i])
        if _GO_MIN_W <= w <= _GO_MAX_W and _GO_MIN_H <= h <= _GO_MAX_H and area > best_area:
            best = (int(round(centroids[i][0])), int(round(centroids[i][1])))
            best_area = area
    return best


def _text_bands(gray: np.ndarray, x0: int, x1: int) -> list[tuple[int, int]]:
    """Vertical [y0, y1) spans of dark-text rows within columns ``[x0, x1)``."""
    on = (gray[:, x0:x1] < _DARK_MAX).mean(axis=1) > _ROW_DARK_FRAC
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for y, lit in enumerate([*list(on), False]):
        if lit and start is None:
            start = y
        elif not lit and start is not None:
            if y - start >= _MIN_BAND_H:
                bands.append((start, y))
            start = None
    return bands


def parse_date_range(text: str) -> tuple[datetime, datetime] | None:
    """Two UTC datetimes from a popup date line (separators already spaces).

    Repairs digit-confusion misreads, then expects ten 1-4 digit groups:
    ``Y M D H M  Y M D H M``. ``HH=24`` rolls into the next day.
    """
    groups = [g for g in re.findall(r"\d+", text.translate(_DIGIT_FIX)) if 1 <= len(g) <= 4]
    if len(groups) < 10:
        return None
    nums = [int(g) for g in groups[:10]]

    def _mk(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
        return datetime(y, mo, d, tzinfo=UTC) + timedelta(hours=h, minutes=mi)

    try:
        return _mk(*nums[:5]), _mk(*nums[5:10])
    except ValueError:
        return None


def parse_popup(image_bgr: np.ndarray, ocr: OcrFn) -> PopupEvent | None:
    """Read ``{name, starts_at, ends_at}`` off an event-detail popup frame.

    Returns ``None`` when no card is found, the title is empty, or the date line
    doesn't yield a valid range — the caller skips and moves on.
    """
    bbox = find_card_bbox(image_bgr)
    if bbox is None:
        return None
    x, y, w, h = bbox
    gray = cv2.cvtColor(image_bgr[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)

    title_bands = _text_bands(gray, _ICON_COL_PX, w - 20)
    if not title_bands:
        return None
    ty0, ty1 = title_bands[0]
    title_crop = image_bgr[y + ty0 - _PAD : y + ty1 + _PAD, x + _ICON_COL_PX : x + w - 25]
    name, _ = ocr(title_crop, preprocess="title_line")
    name = clean_event_name(name)
    if not name:
        return None

    date_bands = [b for b in _text_bands(gray, 12, w - 12) if b[0] > ty1]
    if not date_bands:
        return None
    dy0, dy1 = date_bands[0]
    date_crop = image_bgr[y + dy0 - _PAD : y + dy1 + _PAD, x + 12 : x + w - 12]
    date_text, _ = ocr(date_crop, preprocess="title_line")
    rng = parse_date_range(date_text)
    if rng is None:
        return None
    return PopupEvent(name=name, starts_at=rng[0], ends_at=rng[1])
