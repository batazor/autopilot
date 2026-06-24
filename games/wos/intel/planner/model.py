"""Image-free snapshot of one Intel marker, so the planner stays pure.

The on-screen detection (``games/wos/intel/exec.py:detect_intel_markers``) is cv2-
coupled and returns ``IntelMarker`` objects. The planner must be unit-testable
without images, so it works on this plain :class:`IntelEvent` instead. A live
reader converts detected markers via :func:`from_marker` before planning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IntelEvent:
    """One Intel action pin, reduced to what the planner needs to rank it.

    ``score`` is the template-match confidence (tie-break only). ``x`` / ``y`` are
    the marker centre, kept for a stable deterministic ordering and so the chosen
    batch can be mapped back to tap targets downstream.
    """

    kind: str                # fight | skull | skull_horned | camp | beast
    color: str               # gold | purple | blue | green | unknown
    score: float = 1.0
    x: int = 0
    y: int = 0


def from_marker(marker: Any) -> IntelEvent:
    """Build an :class:`IntelEvent` from a detected ``exec.IntelMarker``.

    Duck-typed (reads ``.kind`` / ``.color`` / ``.score`` / ``.center``) so this
    module never imports the cv2-coupled detector.
    """
    center = marker.center
    return IntelEvent(
        kind=str(marker.kind),
        color=str(getattr(marker, "color", "unknown")),
        score=float(getattr(marker, "score", 1.0)),
        x=int(center.x),
        y=int(center.y),
    )
