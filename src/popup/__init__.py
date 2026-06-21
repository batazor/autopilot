"""Game-wide, template-free pop-up detector.

Detects the two invariants that hold across essentially all native WoS modals —
a Gaussian-blurred scrim around a sharp card, and a top-right dismiss
affordance — then OCR-gates a safe action decision from the card text.

Public API surface is exactly ``PopupDetector``, ``PopupState``, ``PopupKind``.
"""

from __future__ import annotations

from popup.detector import PopupDetector
from popup.models import PopupKind, PopupState

__all__ = ["PopupDetector", "PopupKind", "PopupState"]
