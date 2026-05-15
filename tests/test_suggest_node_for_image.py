"""Coverage for ``navigation.detector.suggest_node_for_image_sync``.

The labeling UI uses this helper to pre-fill the *Screen ID* dropdown with a
detected node id. The helper must:

* Return ``None`` for empty / malformed inputs.
* Return ``None`` when the detector reports ``UNKNOWN``.
* Return the screen id string when the full detector path matches.
* Fall back to the template-only landmark path when the OCR backend raises.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import navigation.detector as detector_mod
from navigation.detector import ScreenName, suggest_node_for_image_sync


def test_returns_none_for_empty_input() -> None:
    assert suggest_node_for_image_sync(np.zeros((0, 0, 3), dtype=np.uint8)) is None
    assert suggest_node_for_image_sync(np.zeros((10,), dtype=np.uint8)) is None  # type: ignore[arg-type]


def test_returns_none_when_detector_unknown(monkeypatch: Any) -> None:
    async def _fake_detect(self: object, _img: np.ndarray) -> ScreenName:  # noqa: ARG001
        return ScreenName.UNKNOWN

    monkeypatch.setattr(detector_mod.ScreenDetector, "detect_screen", _fake_detect)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert suggest_node_for_image_sync(img) is None


def test_returns_screen_id_string_on_full_detect_hit(monkeypatch: Any) -> None:
    async def _fake_detect(self: object, _img: np.ndarray) -> ScreenName:  # noqa: ARG001
        return ScreenName.MAIN_CITY

    monkeypatch.setattr(detector_mod.ScreenDetector, "detect_screen", _fake_detect)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert suggest_node_for_image_sync(img) == "main_city"


def test_falls_back_to_template_when_full_detect_raises(monkeypatch: Any) -> None:
    """OCR backend down → full path raises → template-only path is consulted."""
    async def _broken(self: object, _img: np.ndarray) -> ScreenName:  # noqa: ARG001
        raise RuntimeError("ocr backend offline")

    async def _template_hit(self: object, _img: np.ndarray) -> ScreenName:  # noqa: ARG001
        return ScreenName.BUILDING

    monkeypatch.setattr(detector_mod.ScreenDetector, "detect_screen", _broken)
    monkeypatch.setattr(
        detector_mod.ScreenDetector, "_detect_by_match_landmarks", _template_hit
    )

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert suggest_node_for_image_sync(img) == "building"


def test_returns_none_when_both_paths_fail(monkeypatch: Any) -> None:
    async def _broken(self: object, _img: np.ndarray) -> ScreenName:  # noqa: ARG001
        raise RuntimeError("dead")

    monkeypatch.setattr(detector_mod.ScreenDetector, "detect_screen", _broken)
    monkeypatch.setattr(
        detector_mod.ScreenDetector, "_detect_by_match_landmarks", _broken
    )

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert suggest_node_for_image_sync(img) is None


def _screen_param(name: str, expected: str) -> Any:
    """Skip a parametrize row when ScreenName is missing the member.

    ``ScreenName`` is built dynamically from ``screen_verify.yaml``; entries get
    commented out mid-refactor. Resolving them at collection time would crash
    the file before the unrelated tests above can run.
    """
    member = getattr(ScreenName, name, None)
    if member is None:
        return pytest.param(
            None,
            expected,
            marks=pytest.mark.skip(
                reason=f"ScreenName.{name} missing — screen_verify.yaml entry absent"
            ),
            id=f"missing:{name}",
        )
    return pytest.param(member, expected, id=name)


@pytest.mark.parametrize(
    "screen,expected",
    [
        _screen_param("MAIL", "mail"),
        _screen_param("HERO_RECRUTMENT", "hero.recrutment"),
        _screen_param("SUGGESTION_BOX", "suggestion_box"),
    ],
)
def test_returns_canonical_screen_id_string(
    monkeypatch: Any, screen: ScreenName, expected: str
) -> None:
    async def _fake_detect(self: object, _img: np.ndarray) -> ScreenName:  # noqa: ARG001
        return screen

    monkeypatch.setattr(detector_mod.ScreenDetector, "detect_screen", _fake_detect)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert suggest_node_for_image_sync(img) == expected
