"""Active-tab detection regression for the chat screen.

`references/chat.alliance.png` is a real capture with the Alliance tab active.
The detector must read the tabs apart via the programmatic `tab_active` HSV
check — which only works because the `max_mean_saturation: 70` override in
screen_verify.yaml is forwarded through to the overlay engine (chat's inactive
tabs sit at S_mean≈115, above the mail-calibrated default of 120).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE = _REPO_ROOT / "references" / "chat.alliance.png"

if not hasattr(ScreenName, "CHAT_ALLIANCE"):
    pytest.skip(
        "ScreenName.CHAT_ALLIANCE missing — chat screen_verify.yaml commented out",
        allow_module_level=True,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detects_active_alliance_tab() -> None:
    assert _REFERENCE.is_file(), f"reference image missing: {_REFERENCE}"
    image = cv2.imread(str(_REFERENCE))
    assert image is not None, f"failed to decode {_REFERENCE}"

    detected = await ScreenDetector(OcrClient(get_settings())).detect_screen(image)

    # Must land on the Alliance tab specifically, not World/Personal or the
    # bare `chat` parent — i.e. tab_active discriminated correctly.
    assert detected == ScreenName.CHAT_ALLIANCE
