"""Shared fixtures for the RU «Белая мгла» real-frame detection tests.

Every RU regression test needs the same scaffolding: skip when Tesseract or the
``rus`` traineddata is missing, bind the ``wos_ru`` catalog, flush the
screen-verify + landmark caches, and restore everything afterwards so the RU
binding never leaks into sibling tests. Use::

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_frame(ru_catalog) -> None:
        ...

``ru_catalog`` yields nothing — it is pure setup/teardown.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


def require_rus_ocr() -> None:
    """Skip the test when Tesseract or the ``rus`` traineddata is unavailable."""
    from config.loader import get_settings
    from ocr.client import OcrClient

    settings = get_settings()
    cmd = str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract")
    if not shutil.which(cmd):
        pytest.skip(f"Tesseract executable not found: {cmd!r}")
    if "rus" not in OcrClient(settings)._available_langs(cmd):
        pytest.skip("rus traineddata not installed — required to read the RU build")


def _reset_detection_caches() -> None:
    from navigation.detector import ScreenDetector
    from navigation.screen_graph import invalidate_screen_verify_config

    invalidate_screen_verify_config()
    ScreenDetector._landmark_rules_cache.clear()
    ScreenDetector._landmark_rules_cache_fp = None


@pytest.fixture
def ru_catalog_no_ocr() -> Iterator[None]:
    """Bind the wos_ru catalog (+ cache flush) WITHOUT requiring rus OCR.

    For template-only (findIcon) assertions that never read text — they must
    run even on machines without the rus traineddata.
    """
    from services import bind_active_module_catalog, get_active_module_catalog

    prior = get_active_module_catalog()
    bind_active_module_catalog("wos_ru")
    _reset_detection_caches()
    try:
        yield
    finally:
        bind_active_module_catalog(prior)
        _reset_detection_caches()


@pytest.fixture
def ru_catalog(ru_catalog_no_ocr: None) -> Iterator[None]:
    """Bind the wos_ru catalog (+ cache flush) for the test, restore after.

    Also skips when Tesseract / rus traineddata is missing — the default for
    detection tests, whose sibling OCR rules need the RU language.
    """
    require_rus_ocr()
    return
