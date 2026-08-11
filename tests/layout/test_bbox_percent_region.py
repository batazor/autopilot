"""One percent→pixel mapping, not one per module.

``research_center`` truncated and ``alliance.members_parser`` rounded, so the
same percent rect resolved to different pixels depending on which module asked.
The difference is a pixel per edge, which is nothing on a panel and everything
on a level pill.
"""

from __future__ import annotations

import pytest

from layout.bbox_percent import clip_region, region_from_percent
from layout.types import Region

_W, _H = 720, 1280


def test_it_rounds_rather_than_truncating() -> None:
    """The behaviour the two copies disagreed about.

    ``28.05%`` of 720 is 201.96 px. Truncation says 201 and loses the column the
    glyph starts in; rounding says 202.
    """
    assert region_from_percent(28.05, 0, 10, 10, _W, _H).x == 202


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        ((0.0, 0.0, 100.0, 100.0), Region(0, 0, _W, _H)),
        ((50.0, 50.0, 50.0, 50.0), Region(360, 640, 360, 640)),
        ((0.0, 0.0, 0.0, 0.0), Region(0, 0, 0, 0)),
    ],
)
def test_whole_percentages_land_exactly(pct: tuple[float, ...], expected: Region) -> None:
    assert region_from_percent(*pct, _W, _H) == expected


def test_a_rect_running_off_the_frame_is_clamped_not_inverted() -> None:
    """An empty numpy slice OCRs to "" — a silent miss that reads as "the
    element is not on screen" rather than "the crop was off the edge"."""
    clipped = clip_region(Region(700, 1270, 200, 200), _W, _H)

    assert clipped == Region(700, 1270, 20, 10)


def test_a_rect_entirely_off_frame_collapses_to_zero_area() -> None:
    clipped = clip_region(Region(900, 1500, 50, 50), _W, _H)

    assert (clipped.w, clipped.h) == (0, 0)


def test_negative_origin_is_pulled_back_to_the_frame() -> None:
    assert clip_region(Region(-30, -40, 100, 100), _W, _H) == Region(0, 0, 70, 60)


def test_a_rect_already_inside_is_untouched() -> None:
    inside = Region(10, 20, 30, 40)

    assert clip_region(inside, _W, _H) == inside
