"""ScrollEndDetector contract: K identical fingerprints ⇒ end-of-scroll."""
from __future__ import annotations

import numpy as np

from analysis.scroll import ScrollEndDetector, fingerprint_region_bgr


def test_warmup_period_is_not_the_end() -> None:
    d = ScrollEndDetector(repeats_to_end=3, tolerance=0)
    d.push(1)
    d.push(2)
    assert d.is_the_end() is False


def test_three_identical_fingerprints_signals_end() -> None:
    d = ScrollEndDetector(repeats_to_end=3, tolerance=0)
    d.push(0xAAAA)
    d.push(0xAAAA)
    d.push(0xAAAA)
    assert d.is_the_end() is True


def test_distinct_fingerprints_do_not_signal_end() -> None:
    # Use far-apart 64-bit values so default tolerance can't collapse them.
    d = ScrollEndDetector(repeats_to_end=3)
    for fp in (0x0, 0xFFFF_FFFF_FFFF_FFFF, 0xAAAA_AAAA_AAAA_AAAA):
        d.push(fp)
    assert d.is_the_end() is False


def test_progress_then_stall_signals_end_after_window() -> None:
    d = ScrollEndDetector(repeats_to_end=3, tolerance=0)
    d.push(0x0)
    d.push(0xFFFF_FFFF_FFFF_FFFF)
    d.push(0xAAAA)
    assert d.is_the_end() is False
    d.push(0xAAAA)
    d.push(0xAAAA)
    assert d.is_the_end() is True


def test_tolerance_absorbs_small_phash_jitter() -> None:
    d = ScrollEndDetector(repeats_to_end=3, tolerance=2)
    base = 0b1111_0000 << 32
    # Two bits flipped — Hamming distance 2, still considered same page.
    perturbed = base ^ 0b11
    d.push(base)
    d.push(perturbed)
    d.push(base)
    assert d.is_the_end() is True


def test_tolerance_rejects_meaningful_change() -> None:
    d = ScrollEndDetector(repeats_to_end=3, tolerance=2)
    base = 0xAA
    far = base ^ 0xFF  # 8 bits flipped, far above tolerance
    d.push(base)
    d.push(far)
    d.push(base)
    assert d.is_the_end() is False


def test_fingerprint_region_bgr_is_stable_for_identical_input() -> None:
    rng = np.random.default_rng(42)
    patch = rng.integers(0, 256, size=(64, 96, 3), dtype=np.uint8)
    assert fingerprint_region_bgr(patch) == fingerprint_region_bgr(patch.copy())


def test_fingerprint_region_bgr_differs_for_different_images() -> None:
    rng = np.random.default_rng(7)
    a = rng.integers(0, 256, size=(64, 96, 3), dtype=np.uint8)
    b = rng.integers(0, 256, size=(64, 96, 3), dtype=np.uint8)
    assert fingerprint_region_bgr(a) != fingerprint_region_bgr(b)
