"""Unit tests for the word-box TSV parser (no tesseract binary needed)."""
from __future__ import annotations

from ocr.word_boxes import WordBox, parse_tsv_word_boxes

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


def test_parses_word_text_and_bbox() -> None:
    tsv = _tsv(_row(left=300, top=1080, width=120, height=44, conf=91, text="Claim"))
    boxes = parse_tsv_word_boxes(tsv)
    assert len(boxes) == 1
    b = boxes[0]
    assert b.text == "Claim"
    assert b.conf == 0.91
    assert (b.left, b.top, b.width, b.height) == (300, 1080, 120, 44)
    assert b.cx == 360  # 300 + 120/2
    assert b.cy == 1102  # 1080 + 44/2


def test_divides_bbox_by_upscale_scale() -> None:
    # bbox is emitted in upscaled space; parser divides back to original pixels.
    tsv = _tsv(_row(left=600, top=2160, width=240, height=88, conf=80, text="Collect"))
    boxes = parse_tsv_word_boxes(tsv, scale=2.0)
    b = boxes[0]
    assert (b.left, b.top, b.width, b.height) == (300, 1080, 120, 44)


def test_skips_low_conf_and_structural_rows() -> None:
    tsv = _tsv(
        _row(level=4, left=0, top=0, width=10, height=10, conf=-1, text=""),
        _row(left=10, top=10, width=40, height=20, conf=30, text="faint"),
        _row(left=80, top=80, width=80, height=30, conf=88, text="Claim"),
    )
    boxes = parse_tsv_word_boxes(tsv, min_conf=0.5)
    assert [b.text for b in boxes] == ["Claim"]


def test_empty_tsv_returns_no_boxes() -> None:
    assert parse_tsv_word_boxes("") == []
    assert parse_tsv_word_boxes(_HEADER) == []


def test_wordbox_center_rounds() -> None:
    b = WordBox(text="x", conf=1.0, left=10, top=10, width=15, height=15)
    assert b.cx == 18  # 10 + 7.5 -> 17.5 -> 18
    assert b.cy == 18
