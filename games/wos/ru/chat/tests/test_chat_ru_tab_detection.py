"""Active-tab detection regression for the RU «Белая мгла» chat screen.

`references/chat.alliance.png` is a real bs1 capture of the Russian build with
the «Альянс» tab active. Under the ``wos_ru`` catalog the RU overlay
(``games/wos/ru/chat/routes/screen_verify.yaml``) replaces the base English
rules, so the detector must:

- read the chat title «Чат» and the tab labels «Мир» / «Альянс» / «Личный» via
  Russian OCR (Tesseract switches to ``rus`` from the active catalog), and
- tell the tabs apart through the ``tab_active`` HSV check, which only works
  because ``max_mean_saturation: 70`` is forwarded (the inactive RU tabs sit at
  S_mean≈120 — «Личный» 118 — so the global default of 120 false-positives it
  as active).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import services
from config.loader import get_settings
from navigation import screen_graph as sg
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE = _REPO_ROOT / "references" / "chat.alliance.png"

if not hasattr(ScreenName, "CHAT_ALLIANCE"):
    pytest.skip(
        "ScreenName.CHAT_ALLIANCE missing — chat screen_verify.yaml commented out",
        allow_module_level=True,
    )


@pytest.fixture
def _wos_ru_catalog():
    """Bind the wos_ru catalog so route discovery and OCR pick the RU overlay."""
    previous = services.get_active_module_catalog()
    services.bind_active_module_catalog("wos_ru")
    sg.invalidate_screen_verify_config()
    try:
        yield
    finally:
        services.bind_active_module_catalog(previous)
        sg.invalidate_screen_verify_config()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detects_active_alliance_tab_ru(_wos_ru_catalog) -> None:
    assert _REFERENCE.is_file(), f"reference image missing: {_REFERENCE}"
    image = cv2.imread(str(_REFERENCE))
    assert image is not None, f"failed to decode {_REFERENCE}"

    detected = await ScreenDetector(OcrClient(get_settings())).detect_screen(image)

    # Must land on the «Альянс» tab specifically — not «Мир»/«Личный» or the
    # bare `chat` parent — i.e. the RU OCR label + tab_active discriminated.
    assert detected == ScreenName.CHAT_ALLIANCE
