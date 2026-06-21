"""Unit tests for the digit-marker TSV parser (no tesseract binary needed)."""
from __future__ import annotations

from ocr.digit_markers import parse_tsv_markers

_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def _row(
    *,
    level: int = 5,
    block: int = 1,
    par: int = 1,
    line: int = 1,
    word: int = 1,
    left: int,
    top: int,
    width: int,
    height: int,
    conf: float,
    text: str,
) -> str:
    return (
        f"{level}\t1\t{block}\t{par}\t{line}\t{word}\t"
        f"{left}\t{top}\t{width}\t{height}\t{conf}\t{text}"
    )


def _tsv(*rows: str) -> str:
    return "\n".join([_HEADER, *rows])


def test_parses_value_center_and_conf() -> None:
    tsv = _tsv(_row(left=100, top=200, width=20, height=30, conf=90, text="1"))
    markers = parse_tsv_markers(tsv, 1000, 1000)
    assert len(markers) == 1
    m = markers[0]
    assert m.value == 1
    assert m.x_pct == 11.0  # (100 + 20/2) / 1000 * 100
    assert m.y_pct == 21.5  # (200 + 30/2) / 1000 * 100
    assert m.conf == 0.9


def test_skips_negative_conf_and_out_of_range() -> None:
    tsv = _tsv(
        _row(left=10, top=10, width=10, height=10, conf=-1, text="9"),
        _row(left=50, top=50, width=10, height=10, conf=88, text="150"),
        _row(left=80, top=80, width=10, height=10, conf=88, text="7"),
    )
    markers = parse_tsv_markers(tsv, 1000, 1000, max_value=99)
    assert [m.value for m in markers] == [7]


def test_merges_split_single_digits_on_a_line() -> None:
    # "3" then "5" adjacent on the same line -> 35.
    tsv = _tsv(
        _row(block=2, line=1, word=1, left=600, top=400, width=18, height=30, conf=70, text="3"),
        _row(block=2, line=1, word=2, left=620, top=400, width=18, height=30, conf=60, text="5"),
    )
    markers = parse_tsv_markers(tsv, 1000, 1000)
    assert [m.value for m in markers] == [35]


def test_does_not_merge_distant_digits() -> None:
    tsv = _tsv(
        _row(block=3, line=1, word=1, left=100, top=10, width=18, height=30, conf=80, text="4"),
        _row(block=3, line=1, word=2, left=400, top=10, width=18, height=30, conf=80, text="8"),
    )
    markers = parse_tsv_markers(tsv, 1000, 1000)
    assert [m.value for m in markers] == [4, 8]


def test_dedups_by_value_keeping_higher_conf() -> None:
    tsv = _tsv(
        _row(block=1, left=100, top=100, width=10, height=10, conf=50, text="1"),
        _row(block=2, left=800, top=800, width=10, height=10, conf=90, text="1"),
    )
    markers = parse_tsv_markers(tsv, 1000, 1000)
    assert len(markers) == 1
    assert markers[0].value == 1
    assert markers[0].conf == 0.9


def test_filters_by_min_conf_and_scales_geometry() -> None:
    tsv = _tsv(
        _row(left=200, top=200, width=40, height=40, conf=20, text="5"),
        _row(left=400, top=400, width=40, height=40, conf=80, text="6"),
    )
    # Image was OCR'd at 2x; original is 500x500.
    markers = parse_tsv_markers(tsv, 500, 500, scale=2.0, min_conf=0.30)
    assert [m.value for m in markers] == [6]
    m = markers[0]
    # center px / scale = (400 + 20)/2 = 210 -> 42% of 500
    assert m.x_pct == 42.0
    assert m.left == 200  # 400 / 2


def test_empty_and_headerless_tsv() -> None:
    assert parse_tsv_markers("", 100, 100) == []
    assert parse_tsv_markers("garbage", 100, 100) == []
    assert parse_tsv_markers(_HEADER, 0, 0) == []
