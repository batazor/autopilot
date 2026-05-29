"""Mean-centered masked NCC verification for the direct-template findIcon path.

``TM_CCORR_NORMED`` (used to locate the masked peak) is not mean-centered, so a
uniformly bright window scores near 1.0 against a bright template. The direct
path now also requires a mean-centered masked NCC to clear threshold, which
rejects those structural false positives.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from analysis.overlay import evaluate_overlay_rules
from analysis.overlay_engine import _masked_zero_mean_ncc

if TYPE_CHECKING:
    from pathlib import Path


def _structured_template() -> np.ndarray:
    """A bright, saturated, horizontally-varying BGR template (has gray variance).

    Bright enough that a uniform window scores high on TM_CCORR_NORMED, but
    saturated (not white) so the bright-detail gate stays out of the way — this
    isolates the zero-mean NCC check.
    """
    tpl = np.zeros((10, 10, 3), dtype=np.uint8)
    for col in range(10):
        tpl[:, col] = (0, 90 + col * 16, 200)  # B=0, G ramps, R=200
    return tpl


def test_zero_mean_ncc_exact_match_is_one() -> None:
    tpl = _structured_template()
    assert _masked_zero_mean_ncc(tpl, tpl.copy(), None) > 0.999


def test_zero_mean_ncc_flat_template_defers_to_one() -> None:
    flat = np.full((10, 10, 3), (0, 200, 200), dtype=np.uint8)
    # Nothing structural to verify → defer to the locating score.
    assert _masked_zero_mean_ncc(flat, np.zeros_like(flat), None) == 1.0


def test_zero_mean_ncc_uniform_window_against_structured_template_is_low() -> None:
    tpl = _structured_template()
    uniform_bright = np.full((10, 10, 3), (0, 200, 200), dtype=np.uint8)
    assert _masked_zero_mean_ncc(tpl, uniform_bright, None) < 0.2


def test_zero_mean_ncc_respects_mask() -> None:
    tpl = _structured_template()
    # Mask out everything but a single uniform column → no variance under mask.
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[:, 3] = 255
    # A single column is uniform vertically → template has no masked variance →
    # defers to 1.0 rather than producing a spurious score.
    assert _masked_zero_mean_ncc(tpl, np.zeros_like(tpl), mask) == 1.0


def _area_doc() -> dict:
    return {
        "screens": [
            {
                "id": 1,
                "screen_id": "main_city",
                "ocr": "references/main_city.png",
                "regions": [
                    {
                        "name": "main_city.icon_search",
                        "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                    }
                ],
            }
        ]
    }


def _rule() -> list[dict]:
    return [
        {
            "name": "module.event.icon.visible",
            "region": "main_city.icon_search",
            "template": "games/wos/events/trials/references/event.trials.png",
            "action": "findIcon",
            "threshold": 0.85,
        }
    ]


def test_direct_template_rejects_bright_window_false_positive(tmp_path: Path) -> None:
    """A uniform bright search region must NOT match a structured template,
    even though TM_CCORR_NORMED scores high on it."""
    repo = tmp_path
    template_path = repo / "games/wos/events/trials/references/event.trials.png"
    template_path.parent.mkdir(parents=True)
    cv2.imwrite(str(template_path), _structured_template())

    # Search region is a flat bright field — no real icon present.
    frame = np.full((100, 100, 3), (0, 200, 200), dtype=np.uint8)

    out = evaluate_overlay_rules(frame, _area_doc(), repo, _rule())
    hit = out["module.event.icon.visible"]

    assert hit["matched"] is False
    # The locating correlation is high, but the structural NCC is what gates it.
    assert hit["score"] >= 0.85
    assert hit["score_ncc"] < 0.85


def test_direct_template_accepts_real_match(tmp_path: Path) -> None:
    """Positive control: the actual template present in the frame still matches."""
    repo = tmp_path
    template_path = repo / "games/wos/events/trials/references/event.trials.png"
    template_path.parent.mkdir(parents=True)
    tpl = _structured_template()
    cv2.imwrite(str(template_path), tpl)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[40:50, 30:40] = tpl

    out = evaluate_overlay_rules(frame, _area_doc(), repo, _rule())
    hit = out["module.event.icon.visible"]

    assert hit["matched"] is True
    assert hit["top_left"] == [30, 40]
    assert hit["score_ncc"] > 0.99
