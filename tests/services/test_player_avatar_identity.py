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
