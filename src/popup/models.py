"""Data models for the game-wide pop-up detector.

These mirror the existing ``layout.types`` / perception style: frozen
dataclasses, not Pydantic. The detector localizes a modal and classifies it;
geometry alone never decides a *safe* action — see :mod:`popup.classify`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from layout.types import Point, Region


class PopupKind(StrEnum):
    """Outcome of classifying a localized modal.

    The ordering of the values has no semantics — classification order lives in
    :class:`popup.classify.SafetyClassifier`.
    """

    NONE = "none"  # no modal detected
    SAFE_DISMISS = "safe_dismiss"  # close via X / "Got it" / "Later"
    REWARD_CLAIM = "reward_claim"  # has Claim/Confirm, no X — tap the claim button
    PURCHASE = "purchase"  # price/Buy/Spend present — NEVER tap CTA, only X
    CAPTCHA = "captcha"  # route to 2captcha handler, do NOT dismiss
    AD_WEBVIEW = "ad_webview"  # full-bleed, no blurred scrim — model fallback
    UNKNOWN_MODAL = "unknown_modal"  # overlay present but unclassified — escalate-aware


@dataclass(frozen=True, slots=True)
class DetectionSignals:
    """Screen-agnostic signals derived from the sharpness mask + bbox.

    All values are normalized so they hold across resolutions:

    - ``card_frac`` — modal area / frame area. Distinguishes a large blocking
      modal from a small toast.
    - ``center`` — ``(cx/W, cy/H)`` of the modal bbox. Native modals sit roughly
      centered.
    - ``scrim_sharp`` — fraction of *sharp* pixels in a ring just outside the
      card. Near 0 ⇒ the surround is cleanly blurred ⇒ a real modal. This is the
      key discriminator that luminance cannot provide.
    - ``overlay_present`` — the final gate: ``scrim_sharp`` low **and**
      ``card_frac`` inside the accepted band.
    """

    card_frac: float
    center: tuple[float, float]
    scrim_sharp: float
    overlay_present: bool


@dataclass(frozen=True, slots=True)
class PopupState:
    """Full result of a single detection pass over one frame."""

    kind: PopupKind
    bbox: Region | None  # full modal rect, None if no modal
    close_point: Point | None  # tap target for the X (top-right of bbox)
    primary_point: Point | None  # Claim/Confirm CTA, only set for REWARD_CLAIM
    card_text: str  # OCR'd card text (lowercased, joined)
    signals: DetectionSignals
