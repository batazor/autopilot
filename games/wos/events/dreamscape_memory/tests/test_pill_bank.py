"""Invariants of the pill template bank (store→match roundtrip, gates, dedup).

Synthetic pills are drawn with the real fill centroids so the tests exercise
the actual store/match pipeline — active-state gate, tight-text extraction,
threshold gates, index/PNG persistence — without shipping game frames.
Threshold *values* are calibration data and are not restated here; what must
hold regardless of tuning is: a stored rendering matches itself shifted within
the band, a different word does not, a struck pill neither stores nor matches,
and re-storing an already-covered rendering is refused.
"""

from __future__ import annotations

import cv2
import numpy as np
from games.wos.events.dreamscape_memory.solver.constants import (
    _PILL_BG_ACTIVE_BGR,
    _PILL_BG_STRUCK_BGR,
)
from games.wos.events.dreamscape_memory.solver.pill_bank import PillTemplateBank


def _pill_band(text: str, *, fill=_PILL_BG_ACTIVE_BGR, shift: int = 0) -> np.ndarray:
    """A synthetic slot band: pill fill with white glyph text, optionally shifted.

    A mild vertical gradient keeps the fill realistic — the state classifier
    medians the brighter-than-mean half of the band, which on a perfectly flat
    fill would collapse to the text pixels alone (real pills are shaded).
    """
    band = np.zeros((80, 205, 3), dtype=np.uint8)
    band[:] = np.array(fill, dtype=np.uint8)
    ramp = np.linspace(14, -14, band.shape[0], dtype=np.int16)[:, None, None]
    band = np.clip(band.astype(np.int16) + ramp, 0, 255).astype(np.uint8)
    cv2.putText(
        band,
        text,
        (18 + shift, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return band


def test_store_match_roundtrip_and_gates(tmp_path):
    bank = PillTemplateBank(tmp_path / "bank")
    assert bank.store("bell", "Bell", _pill_band("BELL"), source="test")

    # The same rendering shifted inside the band still resolves to its key…
    hit = bank.match(_pill_band("BELL", shift=5), {"bell", "fork"})
    assert hit is not None and hit.key == "bell" and hit.word == "Bell"

    # …but only when the key is among the expected set.
    assert bank.match(_pill_band("BELL", shift=5), {"fork"}) is None

    # A different word never borrows the template.
    assert bank.match(_pill_band("FORK"), {"bell", "fork"}) is None

    # A struck pill is not an active rendering: no match, no store.
    struck = _pill_band("BELL", fill=_PILL_BG_STRUCK_BGR)
    assert bank.match(struck, {"bell"}) is None
    assert not bank.store("bell2", "Bell", struck)

    # A rendering the bank already covers is refused (no variant spam).
    assert not bank.store("bell", "Bell", _pill_band("BELL", shift=3))


def test_bank_persists_across_instances(tmp_path):
    root = tmp_path / "bank"
    assert PillTemplateBank(root).store("bell", "Bell", _pill_band("BELL"))

    reloaded = PillTemplateBank(root)
    hit = reloaded.match(_pill_band("BELL", shift=4), {"bell"})
    assert hit is not None and hit.key == "bell"
