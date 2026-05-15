"""Shared types for OmniParser HTTP API (see microsoft/OmniParser)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ParsedUiElement:
    """One detected UI element (icon or OCR text box)."""

    type: Literal["icon", "text"]
    bbox: tuple[float, float, float, float]
    """Normalized ``xyxy`` in ``[0, 1]`` (fractions of image width/height)."""
    interactivity: bool
    content: str
