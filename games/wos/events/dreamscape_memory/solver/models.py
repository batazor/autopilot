"""Typed records used across the Dreamscape Memory solver."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from layout.types import Point


class TapCandidate(NamedTuple):
    raw_word: str
    raw_key: str
    key: str
    point: Point
    region: str = ""


class PendingClick(NamedTuple):
    key: str
    raw_key: str
    raw_word: str
    point: Point
    # time.monotonic() of the last tap sent for this slot. The pill-template
    # bank only credits a grey that lands within the learn window of OUR tap
    # (the operator plays alongside; a late grey is a human find).
    tapped_at: float = 0.0


class SlotFsmState(NamedTuple):
    status: str
    raw_word: str = ""
    raw_key: str = ""
    key: str = ""
    point: Point | None = None


class FuzzyLookup(NamedTuple):
    key: str | None
    ambiguous: bool = False


class HelpTargetTap(NamedTuple):
    word: str
    point: Point
    # time.monotonic() of the highlight tap: the grey must arrive within the
    # learn window or the find is credited to the operator, not to this tap.
    tapped_at: float = 0.0


class HelpMotionCandidate(NamedTuple):
    point: Point
    score: float


class PillCropCandidate(NamedTuple):
    """Slot band crop held from OCR-read time until the tap colour-confirms.

    The confirm frame shows the pill already struck, so the ACTIVE rendering
    the template bank needs must be captured when the word is read and only
    persisted once the game confirms the word was right.
    """

    key: str
    word: str
    crop: object  # np.ndarray (BGR slot band from the lossless OCR frame)
