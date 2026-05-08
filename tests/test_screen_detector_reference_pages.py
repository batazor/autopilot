from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from navigation.detector import ScreenDetector, ScreenName

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("chief_profile.png", ScreenName.CHIEF_PROFILE),
        ("mail_page.png", ScreenName.MAIL),
        ("isNewPeople.png", ScreenName.MAIN_CITY),
    ],
)
async def test_screen_detector_identifies_reference_pages(
    filename: str,
    expected: ScreenName,
) -> None:
    path = _REPO_ROOT / "references" / filename
    assert path.is_file(), f"reference image missing: {path}"
    image = cv2.imread(str(path))
    assert image is not None, f"failed to decode {path}"

    detected = await ScreenDetector().detect_screen(image)

    assert detected == expected
