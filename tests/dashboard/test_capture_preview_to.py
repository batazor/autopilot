from __future__ import annotations

from typing import TYPE_CHECKING

from dashboard import reference_preview as rp

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_capture_preview_to_uses_rolling_when_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "shot.png"

    def _copy(_iid: str, dest: Path, **_: object) -> tuple[bool, str]:
        dest.write_bytes(b"from-rolling")
        return True, ""

    monkeypatch.setattr(rp, "copy_rolling_preview_to", _copy)
    monkeypatch.setattr(rp, "_adb_screencap_to_target", lambda *_a, **_k: (False, "adb"))

    ok, msg = rp.capture_preview_to("bs1", target)
    assert ok is True
    assert msg == ""
    assert target.read_bytes() == b"from-rolling"


def test_capture_preview_to_falls_back_to_adb_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "shot.png"

    monkeypatch.setattr(
        rp,
        "copy_rolling_preview_to",
        lambda *_a, **_k: (False, "rolling preview for 'bs1' is ~19s old"),
    )

    def _adb(_iid: str, dest: Path, *, rolling_msg: str = "") -> tuple[bool, str]:
        dest.write_bytes(b"from-adb")
        return True, ""

    monkeypatch.setattr(rp, "_adb_screencap_to_target", _adb)

    ok, msg = rp.capture_preview_to("bs1", target)
    assert ok is True
    assert msg == ""
    assert target.read_bytes() == b"from-adb"
