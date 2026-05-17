"""Golden-frame tests for ``navigation.hero_grid_search``.

Parametrises every per-cell assertion across two stable fixtures that
captured different states of the heroes roster:

* ``page_heroes.png`` — only ``bahiti`` + ``sergey`` unlocked, both with
  pending red-dot notifications and skill-upgrade arrows.
* ``page_heroes_3_unlocked.png`` — same roster after the player
  re-sorted the grid: ``bahiti`` / ``molly`` / ``sergey`` line up across
  row 0, each with a red-dot; only ``sergey`` carries an upgrade arrow.
* ``page_heroes_ready_to_recruit.png`` — five unlocked heroes plus
  ``cloris`` in the "Recruit / 10/10" transition state: shards collected
  to the cap but the hero hasn't been claimed yet. The card is rendered
  in full color (so ``available`` reads True), distinguished from a
  fully-recruited hero only by the level slot showing "Recruit" instead
  of "Lv. N".

The second fixture also exercises the re-sort path — heroes that owned
different ``(row, col)`` slots in the first frame now sit elsewhere, so a
drift in any grid constant flips at least one ``cell`` assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import pytest

from navigation.hero_grid_search import (
    HeroMatch,
    find_hero_in_frame,
    scan_grid_frame,
)

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

# Per-hero golden output captured from ``cmd/test_heroes_grid_match.py`` on
# each fixture. Each row pins ``cell`` / ``xy`` / ``available`` / red-dot /
# upgrade so a regression in any sub-detector fails only the affected
# (frame, hero) pair.
_FRAME_2_UNLOCKED: dict[str, dict] = {
    "bahiti":      {"cell": (0, 0), "xy": (111, 233), "available": True,  "has_red_dot": True,  "upgrade": True},
    "sergey":      {"cell": (0, 1), "xy": (277, 233), "available": True,  "has_red_dot": True,  "upgrade": True},
    "jeronimo":    {"cell": (0, 2), "xy": (443, 233), "available": False, "has_red_dot": False, "upgrade": False},
    "natalia":     {"cell": (0, 3), "xy": (609, 233), "available": False, "has_red_dot": False, "upgrade": False},
    "zinman":      {"cell": (1, 0), "xy": (111, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "molly":       {"cell": (1, 1), "xy": (277, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "ling_xue":    {"cell": (1, 2), "xy": (443, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "lumak_bokan": {"cell": (1, 3), "xy": (609, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "jasser":      {"cell": (2, 0), "xy": (111, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "seo_yoon":    {"cell": (2, 1), "xy": (277, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "gina":        {"cell": (2, 2), "xy": (443, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "jessie":      {"cell": (2, 3), "xy": (609, 795), "available": False, "has_red_dot": False, "upgrade": False},
}
_FRAME_3_UNLOCKED: dict[str, dict] = {
    "bahiti":      {"cell": (0, 0), "xy": (111, 233), "available": True,  "has_red_dot": True,  "upgrade": False},
    "molly":       {"cell": (0, 1), "xy": (277, 233), "available": True,  "has_red_dot": True,  "upgrade": False},
    "sergey":      {"cell": (0, 2), "xy": (443, 233), "available": True,  "has_red_dot": True,  "upgrade": True},
    "jeronimo":    {"cell": (0, 3), "xy": (609, 233), "available": False, "has_red_dot": False, "upgrade": False},
    "natalia":     {"cell": (1, 0), "xy": (111, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "zinman":      {"cell": (1, 1), "xy": (277, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "ling_xue":    {"cell": (1, 2), "xy": (443, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "lumak_bokan": {"cell": (1, 3), "xy": (609, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "jasser":      {"cell": (2, 0), "xy": (111, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "seo_yoon":    {"cell": (2, 1), "xy": (277, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "gina":        {"cell": (2, 2), "xy": (443, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "jessie":      {"cell": (2, 3), "xy": (609, 795), "available": False, "has_red_dot": False, "upgrade": False},
}
_FRAME_READY_TO_RECRUIT: dict[str, dict] = {
    "cloris":      {"cell": (0, 0), "xy": (111, 233), "available": True,  "has_red_dot": False, "upgrade": False},
    "bahiti":      {"cell": (0, 1), "xy": (277, 233), "available": True,  "has_red_dot": True,  "upgrade": False},
    "molly":       {"cell": (0, 2), "xy": (443, 233), "available": True,  "has_red_dot": True,  "upgrade": False},
    "patrick":     {"cell": (0, 3), "xy": (609, 233), "available": True,  "has_red_dot": True,  "upgrade": True},
    "sergey":      {"cell": (1, 0), "xy": (111, 514), "available": True,  "has_red_dot": True,  "upgrade": True},
    "jeronimo":    {"cell": (1, 1), "xy": (277, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "natalia":     {"cell": (1, 2), "xy": (443, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "zinman":      {"cell": (1, 3), "xy": (609, 514), "available": False, "has_red_dot": False, "upgrade": False},
    "ling_xue":    {"cell": (2, 0), "xy": (111, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "lumak_bokan": {"cell": (2, 1), "xy": (277, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "jasser":      {"cell": (2, 2), "xy": (443, 795), "available": False, "has_red_dot": False, "upgrade": False},
    "seo_yoon":    {"cell": (2, 3), "xy": (609, 795), "available": False, "has_red_dot": False, "upgrade": False},
}
_FRAMES: dict[str, tuple[str, dict[str, dict]]] = {
    "frame_2_unlocked": ("page_heroes.png", _FRAME_2_UNLOCKED),
    "frame_3_unlocked": ("page_heroes_3_unlocked.png", _FRAME_3_UNLOCKED),
    "frame_ready_to_recruit": ("page_heroes_ready_to_recruit.png", _FRAME_READY_TO_RECRUIT),
}

_MIN_SCORE = 0.85


def _frame_hero_params() -> list[Any]:
    """Cross-product ``(frame_label, hero_id)`` for per-hero parametrize.

    Using ``pytest.param`` so the test id reads ``[frame_2_unlocked-bahiti]``
    — easy to grep when a single combination regresses.
    """
    out: list[Any] = []
    for frame_label, (_, expected) in _FRAMES.items():
        for hero_id in sorted(expected):
            out.append(pytest.param(frame_label, hero_id, id=f"{frame_label}-{hero_id}"))
    return out


@pytest.fixture(scope="module")
def all_scans() -> dict[str, dict[str, HeroMatch]]:
    """Pre-scan every fixture once; tests look up by frame label.

    Module-scoped because ``scan_grid_frame`` against a 62-template
    registry takes ~0.3s per frame — repeating that for every
    parametrized assertion would dominate the suite runtime.
    """
    out: dict[str, dict[str, HeroMatch]] = {}
    for label, (filename, _) in _FRAMES.items():
        path = _FIXTURES_DIR / filename
        assert path.is_file(), f"missing fixture: {path}"
        frame = cv2.imread(str(path))
        assert frame is not None, f"cannot decode {path}"
        out[label] = scan_grid_frame(frame, threshold=0.7)
    return out


@pytest.fixture(scope="module")
def all_frames() -> dict[str, Any]:
    """Decoded BGR arrays for tests that hit ``find_hero_in_frame`` directly."""
    out: dict[str, Any] = {}
    for label, (filename, _) in _FRAMES.items():
        out[label] = cv2.imread(str(_FIXTURES_DIR / filename))
    return out


@pytest.mark.parametrize("frame_label", list(_FRAMES))
def test_scan_grid_frame_detects_every_expected_hero(
    all_scans: dict[str, dict[str, HeroMatch]], frame_label: str
) -> None:
    expected = _FRAMES[frame_label][1]
    hits = all_scans[frame_label]
    missing = sorted(set(expected) - set(hits))
    assert not missing, f"[{frame_label}] heroes missing from scan output: {missing}"


@pytest.mark.parametrize("frame_label", list(_FRAMES))
def test_scan_grid_frame_has_no_unexpected_extras(
    all_scans: dict[str, dict[str, HeroMatch]], frame_label: str
) -> None:
    expected = _FRAMES[frame_label][1]
    hits = all_scans[frame_label]
    extras = sorted(set(hits) - set(expected))
    assert not extras, f"[{frame_label}] unexpected heroes detected: {extras}"


@pytest.mark.parametrize(("frame_label", "hero_id"), _frame_hero_params())
def test_hero_cell_layout_matches_golden(
    all_scans: dict[str, dict[str, HeroMatch]], frame_label: str, hero_id: str
) -> None:
    expected = _FRAMES[frame_label][1][hero_id]
    m = all_scans[frame_label].get(hero_id)
    assert m is not None, f"[{frame_label}] {hero_id} not detected"
    assert m.cell == expected["cell"], (
        f"[{frame_label}] {hero_id} cell drift: got {m.cell}, expected {expected['cell']}"
    )
    assert m.xy == expected["xy"], (
        f"[{frame_label}] {hero_id} tap-center drift: got {m.xy}, expected {expected['xy']}"
    )
    assert m.score >= _MIN_SCORE, (
        f"[{frame_label}] {hero_id} NCC dropped: {m.score:.3f} < {_MIN_SCORE}"
    )


@pytest.mark.parametrize(("frame_label", "hero_id"), _frame_hero_params())
def test_hero_unlocked_flag_matches_golden(
    all_scans: dict[str, dict[str, HeroMatch]], frame_label: str, hero_id: str
) -> None:
    expected = _FRAMES[frame_label][1][hero_id]
    m = all_scans[frame_label][hero_id]
    assert m.available is expected["available"], (
        f"[{frame_label}] {hero_id} available flag flipped: got {m.available} "
        f"(mean_v={m.mean_v:.1f}), expected {expected['available']}"
    )


@pytest.mark.parametrize(("frame_label", "hero_id"), _frame_hero_params())
def test_hero_red_dot_matches_golden(
    all_scans: dict[str, dict[str, HeroMatch]], frame_label: str, hero_id: str
) -> None:
    expected = _FRAMES[frame_label][1][hero_id]
    m = all_scans[frame_label][hero_id]
    assert m.has_red_dot is expected["has_red_dot"], (
        f"[{frame_label}] {hero_id} red-dot detection drifted: got {m.has_red_dot}, "
        f"expected {expected['has_red_dot']} (bbox={m.red_dot_bbox})"
    )


@pytest.mark.parametrize(("frame_label", "hero_id"), _frame_hero_params())
def test_hero_upgrade_arrow_matches_golden(
    all_scans: dict[str, dict[str, HeroMatch]], frame_label: str, hero_id: str
) -> None:
    expected = _FRAMES[frame_label][1][hero_id]
    m = all_scans[frame_label][hero_id]
    assert m.upgrade_available is expected["upgrade"], (
        f"[{frame_label}] {hero_id} upgrade-arrow detection drifted: got {m.upgrade_available}, "
        f"expected {expected['upgrade']} (bbox={m.upgrade_bbox})"
    )


@pytest.mark.parametrize("frame_label", list(_FRAMES))
def test_find_hero_in_frame_matches_scan(
    all_frames: dict[str, Any],
    all_scans: dict[str, dict[str, HeroMatch]],
    frame_label: str,
) -> None:
    """``find_hero_in_frame`` must agree with ``scan_grid_frame`` per hero."""
    frame = all_frames[frame_label]
    hits = all_scans[frame_label]
    expected = _FRAMES[frame_label][1]
    for hero_id, exp in expected.items():
        single = find_hero_in_frame(frame, hero_id, threshold=0.7)
        assert single is not None, f"[{frame_label}] {hero_id} not found by find_hero_in_frame"
        assert single.cell == exp["cell"]
        assert single.xy == exp["xy"]
        scanned = hits[hero_id]
        assert single.available == scanned.available
        assert single.has_red_dot == scanned.has_red_dot
        assert single.upgrade_available == scanned.upgrade_available


@pytest.mark.parametrize("frame_label", list(_FRAMES))
def test_find_hero_returns_none_for_off_screen_hero(
    all_frames: dict[str, Any], frame_label: str
) -> None:
    """A hero whose portrait is not on this frame must score below threshold.

    ``edith`` isn't in the visible 3×4 window of either reference frame, so
    the matcher should report it as missing instead of latching onto noise.
    """
    miss = find_hero_in_frame(all_frames[frame_label], "edith", threshold=0.7)
    assert miss is None, (
        f"[{frame_label}] expected None for off-screen hero, got "
        f"cell={miss.cell} score={miss.score:.3f}" if miss else ""
    )


def test_three_unlocked_fixture_has_three_unlocked_with_red_dots(
    all_scans: dict[str, dict[str, HeroMatch]],
) -> None:
    """Frame-specific guard: ``page_heroes_3_unlocked.png`` exists precisely
    to cover the 3-unlocked + per-card red-dot state. Pin that property
    explicitly so a future fixture swap can't quietly degrade coverage."""
    hits = all_scans["frame_3_unlocked"]
    unlocked = {hid for hid, m in hits.items() if m.available}
    assert unlocked == {"bahiti", "molly", "sergey"}, unlocked
    for hid in unlocked:
        assert hits[hid].has_red_dot is True, f"{hid} should carry red-dot"
    # Only sergey shows the upgrade-arrow on this frame.
    assert hits["sergey"].upgrade_available is True
    assert hits["bahiti"].upgrade_available is False
    assert hits["molly"].upgrade_available is False
