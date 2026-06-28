"""OCR language selection for alternate builds — the Russian "Белая мгла" build.

Verifies that ``OcrClient`` reads Cyrillic for the ``wos_ru`` module catalog
(``com.gof.globalru``), falls back gracefully when the traineddata is missing,
and that the title-line cleaner no longer strips Cyrillic.
"""
from __future__ import annotations

import numpy as np
import pytest

from config.loader import load_settings
from layout.types import Region
from ocr.client import OcrClient


@pytest.fixture
def client() -> OcrClient:
    # Baked config maps ``wos_ru`` → ``rus`` (see config/_settings_data.py). NOT
    # ``rus+eng``: the mixed dictionary makes Tesseract pick Latin homoglyphs for
    # Cyrillic glyphs ("Барак Ур." → "Bapak Yp."), wrecking the building reader.
    return OcrClient(load_settings())


def test_catalog_lang_config_is_loaded(client: OcrClient) -> None:
    assert client._catalog_lang.get("wos_ru") == "rus"


def test_default_lang_when_catalog_unmapped(client: OcrClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.get_active_module_catalog", lambda: "wos")
    assert client._resolve_lang() == "eng"


def test_ru_build_selects_russian_when_installed(
    client: OcrClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("services.get_active_module_catalog", lambda: "wos_ru")
    monkeypatch.setattr(
        OcrClient, "_available_langs", staticmethod(lambda _cmd: frozenset({"eng", "rus"}))
    )
    assert client._resolve_lang() == "rus"


def test_ru_build_falls_back_when_traineddata_missing(
    client: OcrClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("services.get_active_module_catalog", lambda: "wos_ru")
    # rus.traineddata not installed → degrade to the default instead of erroring.
    monkeypatch.setattr(
        OcrClient, "_available_langs", staticmethod(lambda _cmd: frozenset({"eng"}))
    )
    assert client._resolve_lang() == "eng"


def test_beta_catalog_keeps_default_lang(client: OcrClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only mapped catalogs switch language; the beta build stays English."""
    monkeypatch.setattr("services.get_active_module_catalog", lambda: "wos_beta")
    monkeypatch.setattr(
        OcrClient, "_available_langs", staticmethod(lambda _cmd: frozenset({"eng", "rus"}))
    )
    assert client._resolve_lang() == "eng"


def test_title_line_cleaning_preserves_cyrillic() -> None:
    # The Russian event banner — the old ``[A-Za-z0-9]`` cleaner wiped it to "".
    assert OcrClient._clean_title_line_text("  Грядет буря 50%") == "Грядет буря"
    assert OcrClient._clean_title_line_text("Альянсовые квесты") == "Альянсовые квесты"


def test_title_line_cleaning_unchanged_for_english() -> None:
    assert OcrClient._clean_title_line_text("  Heroes  ") == "Heroes"
    assert OcrClient._clean_title_line_text("City of Embers 99%") == "City of Embers"


def test_patch_hash_differs_by_language() -> None:
    """Identical pixels under eng vs rus must occupy distinct cache entries."""
    img = np.zeros((20, 40, 3), dtype=np.uint8)
    region = Region(0, 0, 40, 20)
    eng = OcrClient._patch_hash(img, region, preprocess="title_line", lang="eng")
    rus = OcrClient._patch_hash(img, region, preprocess="title_line", lang="rus")
    assert eng != rus
