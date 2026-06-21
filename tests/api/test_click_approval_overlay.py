from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from api.services import click_approval_overlay as overlay

if TYPE_CHECKING:
    from pathlib import Path


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def test_load_preview_metadata_reads_png_header_dimensions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "references" / "temporal" / "bs1_approval_current.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(_png_header(720, 1280))
    rel = path.relative_to(tmp_path).as_posix()

    monkeypatch.setattr(overlay, "repo_root", lambda: tmp_path)

    available, out_rel, mtime, width, height = overlay.load_preview_metadata(
        instance_id="bs1",
        payload={"preview_png_rel": rel},
        source="capture",
    )

    assert available is True
    assert out_rel == rel
    assert mtime is not None
    assert (width, height) == (720, 1280)
