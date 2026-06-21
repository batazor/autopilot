"""Screen history mechanics: LPUSH + LTRIM + dedupe + ``from_screen`` verify.

Together these power the per-hero wiki node verification — we don't have an
OCR landmark on the wiki popup, so the navigator decides we're on
``heroes.<hero>.wiki`` purely from the fact that the previous hop was
``page.heroes.<hero>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from navigation.navigator import _SCREEN_HISTORY_MAX
from tests.navigation.conftest_nav import make_navigator

if TYPE_CHECKING:
    from config.loader import Settings
    from ocr.client import OcrClient


def _fake_capture_and_tap() -> tuple[Any, Any]:
    """Capture / tap stubs — we don't drive an actual navigation in these tests."""

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, _point: Any, *, approval_region: str | None = None) -> bool:
        del approval_region
        return True

    return capture, tap


@pytest.mark.asyncio
async def test_write_screen_pushes_history(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """Each non-empty ``_write_screen`` LPUSHes the new screen."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    await nav._write_screen("bs1", "main_city")
    await nav._write_screen("bs1", "heroes")
    await nav._write_screen("bs1", "page.heroes.ahmose")

    history = await nav._screen_history("bs1")
    assert history == ["page.heroes.ahmose", "heroes", "main_city"]


@pytest.mark.asyncio
async def test_write_screen_dedupes_consecutive(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """Re-writing the same screen back-to-back shouldn't grow the history."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    await nav._write_screen("bs1", "main_city")
    await nav._write_screen("bs1", "main_city")
    await nav._write_screen("bs1", "main_city")
    await nav._write_screen("bs1", "heroes")
    await nav._write_screen("bs1", "heroes")

    history = await nav._screen_history("bs1")
    assert history == ["heroes", "main_city"]


@pytest.mark.asyncio
async def test_write_screen_caps_at_max(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """LTRIM keeps the rolling window bounded at ``_SCREEN_HISTORY_MAX``."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    screens = [f"screen_{i}" for i in range(_SCREEN_HISTORY_MAX + 3)]
    for s in screens:
        await nav._write_screen("bs1", s)

    history = await nav._screen_history("bs1")
    assert len(history) == _SCREEN_HISTORY_MAX
    # Most recent first; the first `len-_MAX` entries fell off the tail.
    assert history[0] == screens[-1]
    assert history[-1] == screens[-_SCREEN_HISTORY_MAX]


@pytest.mark.asyncio
async def test_write_screen_skips_empty_string(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """``_write_screen('')`` (verify-failed signal) must not poison history."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    await nav._write_screen("bs1", "heroes")
    await nav._write_screen("bs1", "")
    await nav._write_screen("bs1", "")
    await nav._write_screen("bs1", "page.heroes.ahmose")

    history = await nav._screen_history("bs1")
    assert history == ["page.heroes.ahmose", "heroes"]


@pytest.mark.asyncio
async def test_from_screen_verify_passes_when_prev_matches(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """``from_screen`` rule passes when the immediate predecessor matches."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    await nav._write_screen("bs1", "page.heroes.ahmose")
    # Verify runs BEFORE ``_write_screen(target)`` — at this point index 0
    # of the history is still the source screen of the just-completed hop.
    ok = await nav._verify_from_screen_rule(
        {"from_screen": ["page.heroes.ahmose"]}, instance_id="bs1"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_from_screen_verify_rejects_unrelated_prev(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """``from_screen`` rule fails when the predecessor doesn't match the rule."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    await nav._write_screen("bs1", "main_city")
    ok = await nav._verify_from_screen_rule(
        {"from_screen": ["page.heroes.ahmose"]}, instance_id="bs1"
    )
    assert ok is False


@pytest.mark.asyncio
async def test_from_screen_verify_accepts_list(redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """List of acceptable predecessors: any one matching is enough."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    await nav._write_screen("bs1", "page.heroes.sergey")
    ok = await nav._verify_from_screen_rule(
        {"from_screen": ["page.heroes.ahmose", "page.heroes.sergey"]},
        instance_id="bs1",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_recover_screen_returns_empty_when_history_empty(
    redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """Cold start: no history to fall back on → no recovery."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)
    assert await nav.recover_screen_from_history("bs1") == ""


@pytest.mark.asyncio
async def test_recover_screen_uses_detector_short_circuit(
    mocker, redis_async: Any
, settings: Settings, ocr_client: OcrClient) -> None:
    """When the screen detector classifies the live frame as the head of
    history, we trust that identity without iterating verify rules."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)
    await nav._write_screen("bs1", "mail")
    # Force current_screen back to empty (the race we're recovering from).
    await redis_async.hset("wos:instance:bs1:state", "current_screen", "")

    from navigation.detector import ScreenName

    async def fake_detect(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName.MAIL

    mocker.patch.object(nav._detector, "detect_screen", new=fake_detect)

    recovered = await nav.recover_screen_from_history("bs1")
    assert recovered == "mail"
    # Recovery also republishes current_screen so the next read sees it.
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")
    assert cur == "mail"


@pytest.mark.asyncio
async def test_recover_screen_skips_from_screen_only_destinations(
    redis_async: Any, settings: Settings, ocr_client: OcrClient) -> None:
    """A history head whose only verify rule is ``from_screen`` (per-hero wiki
    nodes) can't be recovered image-based — those rules look at history's
    *previous* entry, so trusting them here would be circular."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)
    await nav._write_screen("bs1", "heroes.ahmose.wiki")
    await redis_async.hset("wos:instance:bs1:state", "current_screen", "")

    assert await nav.recover_screen_from_history("bs1") == ""


@pytest.mark.asyncio
async def test_recover_screen_returns_empty_when_detection_fails(
    mocker, redis_async: Any
, settings: Settings, ocr_client: OcrClient) -> None:
    """Detector says UNKNOWN and the verify rules don't match → no recovery."""
    cap, tap = _fake_capture_and_tap()
    nav = make_navigator(cap, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)
    await nav._write_screen("bs1", "vip")
    await redis_async.hset("wos:instance:bs1:state", "current_screen", "")

    from navigation.detector import ScreenName

    async def fake_detect(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName.UNKNOWN

    async def fake_verify_rule(*_args: Any, **_kwargs: Any) -> bool:
        return False

    mocker.patch.object(nav._detector, "detect_screen", new=fake_detect)
    mocker.patch.object(nav, "_verify_rule", new=fake_verify_rule)

    assert await nav.recover_screen_from_history("bs1") == ""
