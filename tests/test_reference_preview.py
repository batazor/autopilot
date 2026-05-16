from __future__ import annotations

from pathlib import Path

from ui.reference_preview import list_reference_pngs


def test_list_reference_pngs_skips_unanswerable_assets(tmp_path: Path) -> None:
    ref_root = tmp_path / "references"
    ref_root.mkdir()
    kept = ref_root / "screen.png"
    skipped = ref_root / "crop" / "icon.unanswerable.disabled.png"
    skipped.parent.mkdir()
    kept.write_bytes(b"x")
    skipped.write_bytes(b"y")

    rels = [p.relative_to(ref_root).as_posix() for p in list_reference_pngs(root=ref_root)]

    assert rels == ["screen.png"]


def test_list_reference_pngs_skips_files_removed_during_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ref_root = tmp_path / "references"
    ref_root.mkdir()
    kept = ref_root / "screen.png"
    disappearing = ref_root / "temporal" / ".rolling-wi3whbva.png"
    disappearing.parent.mkdir()
    kept.write_bytes(b"x")
    disappearing.write_bytes(b"y")
    original_stat = Path.stat

    def flaky_stat(self: Path, *args, **kwargs):
        if self.name == ".rolling-wi3whbva.png":
            raise FileNotFoundError(str(self))
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    rels = [p.relative_to(ref_root).as_posix() for p in list_reference_pngs(root=ref_root)]

    assert rels == ["screen.png"]
