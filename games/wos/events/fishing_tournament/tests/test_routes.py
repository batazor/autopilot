"""Screen-graph wiring for the fishing screens (main_ready, gameplay, pause).

Locks in the recognition anchors + safe edges added in Phase 4, and proves the
real reference frames classify correctly (so a reskin/regression is caught).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import pytest

from navigation.screen_graph import route_taps, screen_verify_rules

_REF = Path(__file__).resolve().parents[1] / "references"


def test_main_ready_anchored_on_play_button() -> None:
    rules = screen_verify_rules("main_ready")
    assert any(r.get("match") == "fishing_tournament.play.frosty" for r in rules)


def test_gameplay_anchored_on_title() -> None:
    rules = screen_verify_rules("gameplay")
    assert any(r.get("match") == "fishing_tournament.gameplay.title" for r in rules)


def test_pause_anchored_on_retreat() -> None:
    rules = screen_verify_rules("pause")
    assert any(r.get("match") == "fishing_tournament.retreat" for r in rules)


def test_main_ready_also_anchored_on_go_fish() -> None:
    # The live "resume" hub state (single Go Fish button) must also classify as
    # main_ready, else navigation into the live event fails (navigation_failed).
    rules = screen_verify_rules("main_ready")
    assert any(r.get("match") == "fishing_tournament.go_fish" for r in rules)


def test_new_edges_resolve() -> None:
    # Start a round (Go Fish / either play button → gameplay, via an any_of
    # alt-tap), retreat to the city, and a safety back-out of the minigame.
    assert route_taps("main_ready", "gameplay", game="wos") == [
        [
            {
                "type": "any_of",
                "regions": [
                    "fishing_tournament.go_fish",
                    "fishing_tournament.play.free",
                    "fishing_tournament.play.frosty",
                ],
            }
        ]
    ]
    assert route_taps("main_ready", "main_city", game="wos") == [["icon.page.back"]]
    assert route_taps("gameplay", "main_ready", game="wos") == [[{"type": "system_back"}]]


def test_pause_modal_edges_resolve() -> None:
    # gameplay → pause (tap the pause button), then the modal's two exits:
    # Continue resumes the round, Retreat abandons it back to the event screen.
    assert route_taps("gameplay", "pause", game="wos") == [
        ["fishing_tournament.gameplay.pause"]
    ]
    assert route_taps("pause", "gameplay", game="wos") == [
        ["fishing_tournament.continue"]
    ]
    assert route_taps("pause", "event.fishing_tournament", game="wos") == [
        ["fishing_tournament.retreat"]
    ]


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("main_ready.png", "main_ready"),
        # Live "resume" hub state (single Go Fish button) — must also be main_ready.
        ("main_ready_go_fish.png", "main_ready"),
        ("gameplay.png", "gameplay"),
        # Pause modal — anchored on the Retreat button; wins over gameplay even
        # if the title shows behind it (lower priority value).
        ("pause.png", "pause"),
        # Pre-start promo splash — anchored on the unique "Trial Stages" button.
        ("fishing_tournament.main.png", "event.fishing_tournament"),
    ],
)
def test_reference_frames_classify(ref: str, expected: str) -> None:
    from navigation.detector import ScreenDetector
    from services import get_ocr_client

    frame = cv2.imread(str(_REF / ref))
    assert frame is not None, f"missing reference {ref}"
    detected = asyncio.run(ScreenDetector(get_ocr_client()).detect_screen(frame))
    assert detected == expected
