from __future__ import annotations

from typing import TYPE_CHECKING

from dashboard import reference_preview as rp

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_capture_preview_to_uses_direct_adb_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "shot.png"

    def _capture(_iid: str, dest: Path) -> tuple[bool, str]:
        dest.write_bytes(b"from-adb")
        return True, ""

    monkeypatch.setattr(rp, "capture_adb_screenshot_to", _capture)

    ok, msg = rp.capture_preview_to("bs1", target)
    assert ok is True
    assert msg == ""
    assert target.read_bytes() == b"from-adb"


def test_capture_preview_to_does_not_read_rolling_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "shot.png"

    def _copy(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        msg = "labeling capture must not use rolling preview"
        raise AssertionError(msg)

    def _capture(_iid: str, dest: Path) -> tuple[bool, str]:
        dest.write_bytes(b"fresh")
        return True, ""

    monkeypatch.setattr(rp, "copy_rolling_preview_to", _copy)
    monkeypatch.setattr(rp, "capture_adb_screenshot_to", _capture)

    ok, msg = rp.capture_preview_to("bs1", target)
    assert ok is True
    assert msg == ""
    assert target.read_bytes() == b"fresh"
