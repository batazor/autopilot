"""Per-frame phash memo (layout.template_match.frame_phash64)."""
from __future__ import annotations

import numpy as np

from layout import template_match
from layout.template_match import _phash64, frame_phash64


def _frame(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (1280, 720, 3), dtype=np.uint8)


def test_memo_agrees_with_the_uncached_hash() -> None:
    frame = _frame(1)
    assert frame_phash64(frame) == _phash64(frame)


def test_repeat_call_on_the_same_frame_does_not_recompute(monkeypatch) -> None:
    """The whole point: a rolling tick hashes one frame at three call sites."""
    frame = _frame(2)
    expected = frame_phash64(frame)  # warm the memo

    calls = 0
    real = template_match._phash64

    def counting(patch):
        nonlocal calls
        calls += 1
        return real(patch)

    monkeypatch.setattr(template_match, "_phash64", counting)
    assert frame_phash64(frame) == expected
    assert frame_phash64(frame) == expected
    assert calls == 0


def test_distinct_frames_are_hashed_independently() -> None:
    """A one-entry memo must never answer for a frame it did not hash."""
    a, b = _frame(3), _frame(4)
    ha, hb = frame_phash64(a), frame_phash64(b)
    assert ha == _phash64(a)
    assert hb == _phash64(b)
    assert ha != hb
    # Re-asking for the evicted frame recomputes rather than returning b's hash.
    assert frame_phash64(a) == ha


def test_equal_content_in_a_different_array_still_hashes_correctly() -> None:
    # Identity is the memo key, so a copy misses — it must recompute, not
    # inherit, and either way the answer has to be right.
    frame = _frame(5)
    twin = frame.copy()
    assert frame_phash64(frame) == frame_phash64(twin) == _phash64(frame)
