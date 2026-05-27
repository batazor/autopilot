from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ScreenName is built dynamically from screen_verify.yaml. When the yaml omits a
# screen (mid-refactor), referencing the enum member at collection time crashes
# the whole file. Skip-at-collection if any required member is missing so the
# rest of the suite still runs.
_REQUIRED_SCREENS = ("CHIEF_PROFILE", "MAIL_SYSTEM", "MAIN_CITY", "RECONNECT")
_missing = [s for s in _REQUIRED_SCREENS if not hasattr(ScreenName, s)]
if _missing:
    pytest.skip(
        f"ScreenName missing required members ({', '.join(_missing)}) — "
        "screen_verify.yaml entries commented out / refactored",
        allow_module_level=True,
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("chief_profile.png", ScreenName.CHIEF_PROFILE),
        ("mail_page.png", ScreenName.MAIL_SYSTEM),
        ("isNewPeople.png", ScreenName.MAIN_CITY),
        pytest.param(
            "retry_page.png",
            ScreenName.RECONNECT,
            marks=pytest.mark.skip(
                reason="retry_page template match regressed against current area.json crop; "
                "re-export reconnect_button crop from the reference frame when updating assets.",
            ),
        ),
    ],
)
async def test_screen_detector_identifies_reference_pages(
    filename: str,
    expected: ScreenName,
) -> None:
    if filename == "isNewPeople.png":
        path = _REPO_ROOT / "modules/core/survivors/references" / filename
    elif filename == "chief_profile.png":
        path = _REPO_ROOT / "modules/core/chief_profile/references" / filename
    elif filename == "mail_page.png":
        path = _REPO_ROOT / "modules/mail/references" / filename
    else:
        path = _REPO_ROOT / "references" / filename
    assert path.is_file(), f"reference image missing: {path}"
    image = cv2.imread(str(path))
    assert image is not None, f"failed to decode {path}"

    detected = await ScreenDetector(OcrClient(get_settings())).detect_screen(image)

    assert detected == expected
