"""``scan_hero_details_list`` — portrait-anchored row detection + field parse.

Builds a synthetic Details frame: real wiki portraits dropped at row positions, with a
field→int lookup feeding the level / skill / gear readers. Verifies the pure pipeline
finds each hero by portrait and reads its investment numbers. (The geometry constants are
the live 720×1280 measurement; here we just exercise the detect + parse logic.)
"""
from __future__ import annotations

import cv2
import games.wos.heroes.heroes.scan_hero_details_list as sdl
import numpy as np
import pytest
from games.wos.heroes.heroes.scan_hero_details_list import (
    DetectedRow,
    detect_hero_rows,
    parse_hero_details,
    read_detail_rows_scrolled,
    row_field_bboxes,
)

from config.paths import repo_root
from navigation.hero_grid_search import _all_hero_ids

_WIKI = repo_root() / "db" / "assets" / "wiki" / "heroes"
_ROWS = [h for h in ("edith", "wu_ming", "wayne", "molly") if h in _all_hero_ids()]
_YS = [100, 360, 620, 880]


def _portrait(hero_id: str, px: int = 138) -> np.ndarray:
    imgs = sorted((_WIKI / hero_id).glob("*.*"))
    return cv2.resize(cv2.imread(str(imgs[0])), (px, px), interpolation=cv2.INTER_AREA)


def _synthetic() -> np.ndarray:
    frame = np.full((1180, 720, 3), 22, np.uint8)
    for hid, y in zip(_ROWS, _YS, strict=False):
        frame[y:y + 138, 46:46 + 138] = _portrait(hid)
    return frame


def _nums() -> dict[tuple[int, int, int, int], int | None]:
    nums: dict[tuple[int, int, int, int], int | None] = {}
    for hid, y in zip(_ROWS, _YS, strict=False):
        fb = row_field_bboxes(DetectedRow(hid, 46, y, 1.0))
        nums[fb["level"]] = 80
        for b in fb["skills"]:
            nums[b] = 5
        for b in fb["gears"]:
            nums[b] = 20  # the 4 real gear pieces (the widget is excluded from the geometry)
    return nums


@pytest.mark.skipif(len(_ROWS) < 2, reason="need wiki portraits")
def test_detects_and_parses_each_row():
    frame, nums = _synthetic(), _nums()
    parsed = parse_hero_details(frame, lambda b: nums.get(b))
    assert set(parsed) == set(_ROWS)
    for hid in _ROWS:
        e = parsed[hid]
        assert e["level"] == 80
        assert e["skill"] == 5
        assert e["gear"] == [20, 20, 20, 20]
        assert e["match_score"] > 0.9


def test_detect_finds_rows_top_to_bottom():
    rows = detect_hero_rows(_synthetic())
    assert [r.hero_id for r in rows] == _ROWS
    assert [r.y for r in rows] == _YS


def test_blank_frame_detects_nothing():
    assert detect_hero_rows(np.full((1180, 720, 3), 22, np.uint8)) == []


def test_skill_median_resists_a_single_misread():
    frame, nums = _synthetic(), _nums()
    fb = row_field_bboxes(DetectedRow(_ROWS[0], 46, _YS[0], 1.0))
    nums[fb["skills"][1]] = 1  # one skill misreads low → median of [5,1,5] stays 5
    parsed = parse_hero_details(frame, lambda b: nums.get(b))
    assert parsed[_ROWS[0]]["skill"] == 5


def test_implausible_values_rejected():
    frame, nums = _synthetic(), _nums()
    fb = row_field_bboxes(DetectedRow(_ROWS[0], 46, _YS[0], 1.0))
    nums[fb["level"]] = 540          # OCR garbage > level max → dropped
    for b in fb["gears"]:
        nums[b] = 999                # > gear max → dropped
    parsed = parse_hero_details(frame, lambda b: nums.get(b))
    assert "level" not in parsed[_ROWS[0]]
    assert "gear" not in parsed[_ROWS[0]]


async def test_scrolled_merges_and_orders_across_swipes(monkeypatch):
    # Simulate the scroll: frame 1 reads a,b,c; after a swipe, frame 2 reads c,d,e
    # (overlap on c). The result is deduped, in first-seen (top-to-bottom slot) order,
    # and c's fields are merged across the two frames.
    seqs = [
        {"a": {"match_score": 0.9}, "b": {"match_score": 0.9}, "c": {"match_score": 0.7, "level": 80}},
        {"c": {"match_score": 0.95, "gear": [20]}, "d": {"match_score": 0.9}, "e": {"match_score": 0.9}},
    ]
    calls = {"i": 0}

    async def _fake_read(_frame):
        r = seqs[min(calls["i"], len(seqs) - 1)]
        calls["i"] += 1
        return r

    monkeypatch.setattr(sdl, "read_detail_rows", _fake_read)

    swipes = {"n": 0}

    class _Actions:
        def capture_screen_bgr(self, _inst):
            return np.zeros((10, 10, 3), np.uint8)

        def swipe(self, *_a):
            swipes["n"] += 1
            return True

    ordered = await read_detail_rows_scrolled(_Actions(), "bs1", swipes=1, settle_s=0)
    assert [hid for hid, _ in ordered] == ["a", "b", "c", "d", "e"]
    assert swipes["n"] == 1
    merged = dict(ordered)
    assert merged["c"]["level"] == 80 and merged["c"]["gear"] == [20]  # fields merged
    assert merged["c"]["match_score"] == 0.95  # better-matched read wins
