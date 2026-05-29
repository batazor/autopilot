"""OCR-gated safety classification of a localized modal.

Geometry localizes; *text* decides safety. The card OCR is matched against
allow/deny vocabularies (fuzzy, not substring) consistent with the project's
existing system-dialog handling. The classification order is deliberate and
safety-first: captcha and purchase are checked before any dismiss path so we
never tap a CTA we shouldn't.
"""

from __future__ import annotations

import re

from ocr import fuzzy
from popup.models import DetectionSignals, PopupKind

# A currency glyph or a "$N.NN" amount is a strong purchase signal on its own.
_PRICE_RE = re.compile(r"\$?\d+[.,]\d{2}")
_CURRENCY_RE = re.compile(r"[$€£¥₩]")

DENY_PURCHASE = ["usd", "buy", "purchase", "spend", "gems", "price", "/mo", "subscribe"]
SAFE_DISMISS = ["got it", "close", "later", "skip", "ok", "confirm later"]
REWARD_CUES = ["claim", "collect", "tap to", "received", "reward", "level up"]
CAPTCHA_CUES = ["verify", "select all", "tap the", "captcha", "i am not a robot"]

# Fuzzy threshold for vocabulary matches. High so noisy OCR fragments don't
# accidentally trip a category; we match each whitespace-collapsed line.
_MATCH_THRESHOLD = 0.85


class SafetyClassifier:
    """Map OCR'd card text + signals to a :class:`PopupKind`."""

    def __init__(self, match_threshold: float = _MATCH_THRESHOLD) -> None:
        self._threshold = match_threshold

    def classify(
        self,
        card_text: str,
        signals: DetectionSignals,
        *,
        has_close: bool,
    ) -> PopupKind:
        """Classify a modal. First matching rule wins (safety-first order)."""
        text = card_text.lower()

        if self._has_cue(text, CAPTCHA_CUES):
            return PopupKind.CAPTCHA

        if self._is_purchase(text):
            return PopupKind.PURCHASE

        if self._has_cue(text, REWARD_CUES) and not has_close:
            return PopupKind.REWARD_CLAIM

        if self._has_cue(text, SAFE_DISMISS) or has_close:
            return PopupKind.SAFE_DISMISS

        if signals.overlay_present:
            return PopupKind.UNKNOWN_MODAL

        return PopupKind.NONE

    def _is_purchase(self, text: str) -> bool:
        if _PRICE_RE.search(text) or _CURRENCY_RE.search(text):
            return True
        return self._has_cue(text, DENY_PURCHASE)

    def _has_cue(self, text: str, vocabulary: list[str]) -> bool:
        """True if any line of ``text`` fuzzy-matches a vocabulary entry.

        Matching per line keeps a single noisy multi-line OCR blob from diluting
        the score of the phrase we care about.
        """
        if not text:
            return False
        for line in text.splitlines():
            candidate = " ".join(line.split())
            if not candidate:
                continue
            if fuzzy.match(candidate, vocabulary, threshold=self._threshold, partial=True) is not None:
                return True
        return False
