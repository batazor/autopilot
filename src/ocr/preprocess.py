from __future__ import annotations

from typing import TYPE_CHECKING

import cv2  # type: ignore[import-untyped]
import numpy as np

if TYPE_CHECKING:
    from layout.types import Region

# Upscale factor for CLAHE + Otsu pipelines
# (``enhance`` / ``enhance_line`` / ``title_line`` / ``digits``).
_OCR_BINARY_UPSCALE = 3.0

# Tesseract whitelist for digit-only regions (``digits`` preprocess / ``type: int``).
DIGITS_CHAR_WHITELIST = "0123456789"

# Tesseract whitelist for item word regions. Spaces are restored by TSV line
# joining and final cleanup; keeping them out of the whitelist avoids shell/CLI
# ambiguity around trailing whitespace in the config value.
WORD_CHAR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def crop_region(image: np.ndarray, region: Region) -> np.ndarray:
    return image[region.y : region.y + region.h, region.x : region.x + region.w]


def binary_tile_for_ocr(
    image: np.ndarray,
    *,
    upscale: float = _OCR_BINARY_UPSCALE,
) -> np.ndarray:
    """CLAHE → Otsu → upscale for small UI text (player ids, timers, stats)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return cv2.resize(
        binary,
        None,
        fx=upscale,
        fy=upscale,
        interpolation=cv2.INTER_LINEAR,
    )


def enhance_for_ocr(image: np.ndarray) -> np.ndarray:
    return binary_tile_for_ocr(image)


def digits_for_ocr(image: np.ndarray) -> np.ndarray:
    """Same binarization as ``enhance``; Tesseract uses PSM 8 + digit whitelist."""
    return binary_tile_for_ocr(image)


# White-text threshold for ``bar_timer`` (timer glyphs are pure white; the
# progress-bar fill behind them is saturated green/blue). Otsu picks a split
# inside the busy bar gradient and mangles glyph edges ("4d" → "Ad"), so a
# fixed cut works better here.
_BAR_TIMER_WHITE_THRESHOLD = 160


# Colored badge digits (hero Details popup): the "Lv. N" labels are white-on-orange
# (level) or yellow-on-red (gear) — Otsu on grayscale picks a split inside the busy
# colored badge and mangles the glyphs. Isolate the bright glyph colors (white OR
# yellow) by HSV, render black-on-white, and upscale hard (the badges are ~14 px).
_BADGE_UPSCALE = 6.0
# White text only — tight saturation cap so the light-pink gear badge fill (moderate
# saturation) is NOT swallowed, only the near-white glyphs.
_BADGE_WHITE_LO = (0, 0, 195)
_BADGE_WHITE_HI = (180, 45, 255)
# Yellow/gold gear text.
_BADGE_YELLOW_LO = (16, 110, 160)
_BADGE_YELLOW_HI = (42, 255, 255)


def _badge_mask_ocr(image: np.ndarray, *, white: bool, yellow: bool) -> np.ndarray:
    """Isolate the requested glyph colors → black-on-white, upscaled hard.

    Split white (level/skill text) from yellow (gear text): a combined mask
    cross-contaminates — the yellow component catches a hero portrait's fire glow in
    the level band, the white component catches the gear badge's light border — so the
    reader picks the colour matching each field.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    masks = []
    if white:
        masks.append(cv2.inRange(hsv, np.array(_BADGE_WHITE_LO), np.array(_BADGE_WHITE_HI)))
    if yellow:
        masks.append(cv2.inRange(hsv, np.array(_BADGE_YELLOW_LO), np.array(_BADGE_YELLOW_HI)))
    mask = masks[0] if len(masks) == 1 else cv2.bitwise_or(masks[0], masks[1])
    return cv2.resize(
        255 - mask, None, fx=_BADGE_UPSCALE, fy=_BADGE_UPSCALE, interpolation=cv2.INTER_CUBIC
    )


def badge_white_for_ocr(image: np.ndarray) -> np.ndarray:
    """White glyphs (level / skill "Lv. N") on a colored badge → black-on-white."""
    return _badge_mask_ocr(image, white=True, yellow=False)


def badge_yellow_for_ocr(image: np.ndarray) -> np.ndarray:
    """Yellow/gold glyphs (gear "Lv. N") on a red badge → black-on-white."""
    return _badge_mask_ocr(image, white=False, yellow=True)


def badge_digits_for_ocr(image: np.ndarray) -> np.ndarray:
    """White+yellow combined (generic) — prefer the split modes per field."""
    return _badge_mask_ocr(image, white=True, yellow=True)


def bar_timer_for_ocr(image: np.ndarray) -> np.ndarray:
    """Timer text drawn over a colored progress bar ("4d 11:59:43").

    Keep only the white glyphs (fixed threshold), render them black-on-white,
    and upscale — CLAHE/Otsu pipelines misread the day prefix on the busy
    bar background.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray > _BAR_TIMER_WHITE_THRESHOLD).astype("uint8") * 255
    inverted = 255 - mask
    return cv2.resize(
        inverted,
        None,
        fx=_OCR_BINARY_UPSCALE,
        fy=_OCR_BINARY_UPSCALE,
        interpolation=cv2.INTER_CUBIC,
    )


# Timers: Tesseract single-line (PSM 7), no whitelist — colons must survive.
_FAST_LINE_TYPE_HINTS: frozenset[str] = frozenset({"time"})
# Integer stat cells (player id, power, server id): PSM 7 + digit whitelist so
# Tesseract can't emit a stray glyph (``&``, ``§``…) for an ambiguous digit and
# silently shorten the number once non-digits are stripped downstream.
_FAST_DIGITS_TYPE_HINTS: frozenset[str] = frozenset({"int", "integer"})


def resolve_preprocess(
    explicit: str | None,
    type_hint: str | None,
) -> str | None:
    """Resolve the OCR preprocess pipeline tag for one region call site.

    Resolution order (first non-empty wins):

    1. ``explicit`` — the value set on the rule/step or area.json region by
       the operator (caller picks which one wins, usually rule > region).
    2. Auto-derivation — ``time`` → ``fast_line``; ``int`` / ``integer`` →
       ``fast_digits``. Other types → ``None``.
    3. ``None`` — raw crop, Tesseract block mode (PSM 6).

    ``fast_line`` / ``fast_digits`` are both PSM 7 on the raw crop; only
    ``fast_digits`` adds the digit whitelist. ``word_line`` is raw PSM 7 with an
    English-letter whitelist plus cleanup. ``enhance`` / ``enhance_line`` /
    ``title_line`` / ``digits`` keep Tesseract pipelines with binarization.
    """
    if explicit:
        v = str(explicit).strip().lower()
        if v:
            return v
    if type_hint:
        v = str(type_hint).strip().lower()
        if v in _FAST_DIGITS_TYPE_HINTS:
            return "fast_digits"
        if v in _FAST_LINE_TYPE_HINTS:
            return "fast_line"
    return None


def parse_digit_count(raw: object) -> int | None:
    """``None`` / ``auto`` -> auto width; positive int -> fixed width."""
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
