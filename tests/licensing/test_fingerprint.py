from __future__ import annotations

import re

from licensing.fingerprint import fingerprint_components, generate_fingerprint

_FP_RE = re.compile(r"^[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}$")


def test_fingerprint_format() -> None:
    fp = generate_fingerprint()
    assert _FP_RE.match(fp), f"unexpected format: {fp!r}"


def test_fingerprint_is_stable() -> None:
    assert generate_fingerprint() == generate_fingerprint()


def test_components_returns_expected_keys() -> None:
    parts = fingerprint_components()
    assert set(parts.keys()) == {"machine_id", "mac", "hostname"}
