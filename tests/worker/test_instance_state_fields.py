"""Paired ``<field>`` / ``<field>_at`` mappings, and what age means.

`last_error` and `queue_blocked_reason` carried no timestamp, so a reader could
not tell a two-second-old failure from a three-hour-old one. The pairing is now
a mapping helper rather than two writes a caller has to remember — the
`nav_error` group already showed what happens when one writer knows about a
field and another does not.
"""

from __future__ import annotations

import pytest

from worker.instance_state_fields import (
    field_age_s,
    format_age,
    last_error_mapping,
    queue_blocked_mapping,
    tick_path_mapping,
)


def test_setting_an_error_stamps_it() -> None:
    mapping = last_error_mapping("device offline (ADB)", at=1000.0)

    assert mapping == {"last_error": "device offline (ADB)", "last_error_at": "1000.000000"}


def test_clearing_an_error_clears_the_stamp_too() -> None:
    """A recovered instance must not keep a timestamp pointing at a failure
    that no longer exists — that is the `nav_error` defect in miniature."""
    assert last_error_mapping("") == {"last_error": "", "last_error_at": ""}
    assert last_error_mapping("   ") == {"last_error": "", "last_error_at": ""}


def test_long_errors_are_truncated() -> None:
    """An exception repr can be arbitrarily long and this hash is read on every
    UI poll."""
    mapping = last_error_mapping("x" * 900)

    assert len(mapping["last_error"]) == 500


def test_queue_blocked_pairs_the_same_way() -> None:
    assert queue_blocked_mapping("", at=5.0)["queue_blocked_reason_at"] == ""
    assert queue_blocked_mapping("3 due blocked", at=5.0) == {
        "queue_blocked_reason": "3 due blocked",
        "queue_blocked_reason_at": "5.000000",
    }


@pytest.mark.parametrize("raw", [None, "", "   ", "not-a-number", "0", "-1"])
def test_unusable_stamp_reads_as_unknown_age(raw: str | None) -> None:
    """Unknown is a distinct answer from "old". Callers must show the item."""
    assert field_age_s(raw, now=100.0) is None


def test_age_is_measured_from_the_stamp() -> None:
    assert field_age_s("100.0", now=160.0) == pytest.approx(60.0)


def test_bytes_stamp_is_accepted() -> None:
    """Redis hands back bytes unless the client decodes."""
    assert field_age_s(b"100.0", now=130.0) == pytest.approx(30.0)


def test_clock_skew_does_not_produce_negative_age() -> None:
    assert field_age_s("200.0", now=100.0) == 0.0


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (None, ""),
        (5.0, " (5s ago)"),
        (125.0, " (2m ago)"),
        (7300.0, " (2h ago)"),
        (200000.0, " (2d ago)"),
    ],
)
def test_age_formatting(age: float | None, expected: str) -> None:
    assert format_age(age) == expected


def test_tick_path_is_always_stamped() -> None:
    """A skip path without a stamp cannot be read.

    "the last tick reused its verdict" and "some tick an hour ago reused its
    verdict" look identical without the age, and only the first says the phash
    skip is currently working.
    """
    mapping = tick_path_mapping("skipped_phash", "skipped_phash", at=1000.0)

    assert mapping == {
        "tick_detect_path": "skipped_phash",
        "tick_overlay_path": "skipped_phash",
        "tick_path_at": "1000.000000",
    }


def test_tick_path_stamps_even_when_paths_are_unknown() -> None:
    """Empty paths still carry a stamp — the tick happened, it just had no
    verdict to report, and a reader must be able to age that out."""
    mapping = tick_path_mapping("", "", at=1000.0)

    assert mapping["tick_detect_path"] == ""
    assert mapping["tick_overlay_path"] == ""
    assert mapping["tick_path_at"] == "1000.000000"
