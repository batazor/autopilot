"""Old ``?ref=main_city_v2.png`` deep-links should redirect to base ref + ``?version=v2``."""

from __future__ import annotations

from ui.labeling_version_redirect import resolve_version_ref_redirect


def _doc_with_v2() -> dict:
    return {
        "screens": [
            {
                "id": 1,
                "screen_id": "main_city",
                "ocr": "references/main_city.png",
                "regions": [],
                "versions": [
                    {
                        "id": "v2",
                        "cond": "True",
                        "ocr": "references/main_city_v2.png",
                        "regions": [],
                    }
                ],
            }
        ]
    }


def test_redirects_version_png_to_base_with_version_param() -> None:
    assert resolve_version_ref_redirect(_doc_with_v2(), "main_city_v2.png") == (
        "main_city.png",
        "v2",
    )


def test_no_redirect_when_ref_is_already_base() -> None:
    assert resolve_version_ref_redirect(_doc_with_v2(), "main_city.png") is None


def test_no_redirect_when_ref_unknown() -> None:
    assert resolve_version_ref_redirect(_doc_with_v2(), "totally_unrelated.png") is None


def test_no_redirect_for_empty_or_invalid_ref() -> None:
    doc = _doc_with_v2()
    assert resolve_version_ref_redirect(doc, "") is None
    assert resolve_version_ref_redirect(doc, None) is None
    assert resolve_version_ref_redirect(doc, "..") is None
    assert resolve_version_ref_redirect(doc, "../escape.png") is None


def test_strips_leading_slash_and_backslashes() -> None:
    assert resolve_version_ref_redirect(_doc_with_v2(), "/main_city_v2.png") == (
        "main_city.png",
        "v2",
    )


def test_no_redirect_when_doc_missing_versions() -> None:
    doc = {
        "screens": [
            {"ocr": "references/x.png", "regions": []},
        ]
    }
    assert resolve_version_ref_redirect(doc, "x.png") is None


def test_picks_correct_version_among_multiple() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/screen.png",
                "regions": [],
                "versions": [
                    {"id": "v2", "cond": "True", "ocr": "references/screen_v2.png"},
                    {"id": "v3", "cond": "False", "ocr": "references/screen_v3.png"},
                ],
            }
        ]
    }
    assert resolve_version_ref_redirect(doc, "screen_v3.png") == ("screen.png", "v3")
    assert resolve_version_ref_redirect(doc, "screen_v2.png") == ("screen.png", "v2")
