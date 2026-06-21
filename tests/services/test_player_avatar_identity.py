from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent
from services.player_avatar_identity import (
    MAIN_CITY_AVATAR_BBOX,
    match_player_avatar,
    save_avatar_reference_from_frame,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BS1_MAIN_CITY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "bs1_current_state.png"


def _frame_with_avatar(color: tuple[int, int, int]) -> np.ndarray:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    patch, (left, top) = patch_bgr_from_bbox_percent(frame, MAIN_CITY_AVATAR_BBOX)
    h, w = patch.shape[:2]
    cv2.circle(frame, (left + w // 2, top + h // 2), min(w, h) // 3, color, -1)
    cv2.circle(frame, (left + w // 2, top + h // 2), min(w, h) // 3, (255, 255, 255), 2)
    return frame


def test_match_player_avatar_identifies_saved_reference(tmp_path) -> None:
    save_avatar_reference_from_frame("101", _frame_with_avatar((20, 40, 220)), root=tmp_path)
    save_avatar_reference_from_frame("202", _frame_with_avatar((30, 220, 40)), root=tmp_path)

    match = match_player_avatar(
        _frame_with_avatar((20, 40, 220)),
        candidate_player_ids=["101", "202"],
        root=tmp_path,
    )

    assert match is not None
    assert match.player_id == "101"
    assert match.score >= 0.86


def test_match_player_avatar_honors_candidate_ids(tmp_path) -> None:
    save_avatar_reference_from_frame("101", _frame_with_avatar((20, 40, 220)), root=tmp_path)
    save_avatar_reference_from_frame("202", _frame_with_avatar((30, 220, 40)), root=tmp_path)

    match = match_player_avatar(
        _frame_with_avatar((20, 40, 220)),
        candidate_player_ids=["202"],
        root=tmp_path,
    )

    assert match is None


def test_match_player_avatar_with_bs1_main_city_fixture(tmp_path: Path) -> None:
    frame = cv2.imread(str(BS1_MAIN_CITY_FIXTURE), cv2.IMREAD_COLOR)
    assert frame is not None

    save_avatar_reference_from_frame("401227964", frame, root=tmp_path)

    match = match_player_avatar(
        frame,
        candidate_player_ids=["401227964"],
        root=tmp_path,
    )

    assert match is not None
    assert match.player_id == "401227964"
    assert match.score == 1.0


def test_match_player_avatar_sole_candidate_requires_strong_score(tmp_path: Path) -> None:
    """A lone device-mapped candidate must clear the sole-candidate floor.

    Guards the onboarding case: a fresh account's default avatar must not bind
    to the device's single stale mapped player on a weak (margin-less) match.
    """
    frame = cv2.imread(str(BS1_MAIN_CITY_FIXTURE), cv2.IMREAD_COLOR)
    save_avatar_reference_from_frame("401227964", frame, root=tmp_path)

    # Exact frame (~1.0) binds under the default sole-candidate floor.
    assert (
        match_player_avatar(frame, candidate_player_ids=["401227964"], root=tmp_path)
        is not None
    )
    # Raising the sole-candidate floor above the score vetoes the bind — the
    # safeguard that rejects a default-avatar near-miss with no runner-up.
    assert (
        match_player_avatar(
            frame,
            candidate_player_ids=["401227964"],
            root=tmp_path,
            min_score_sole_candidate=1.01,
        )
        is None
    )
