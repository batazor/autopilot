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


class HelpMotionCandidate(NamedTuple):
    point: Point
    score: float
