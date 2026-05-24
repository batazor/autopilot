"""Overlay-test frame source (live vs reference PNG)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.services.overlay_test import _load_overlay_test_preview

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_load_overlay_test_preview_reference_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = "references/ads/popup.png"
    ref = tmp_path / rel
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(
        "api.services.gallery_api.read_gallery_image",
        lambda _rel: ref.read_bytes(),
    )
    monkeypatch.setattr("api.services.overlay_test.repo_root", lambda: tmp_path)

    png, out_rel, mtime, src = _load_overlay_test_preview(
        instance_id="bs1",
        preview_source="reference",
        preview_rel=rel,
    )
    assert src == "reference"
    assert png is not None
    assert out_rel == rel
    assert mtime is not None


def test_load_overlay_test_preview_reference_missing() -> None:
    png, _rel, mtime, src = _load_overlay_test_preview(
        instance_id="bs1",
        preview_source="reference",
        preview_rel="references/no/such.png",
    )
    assert src == "reference"
    assert png is None
    assert mtime is None
