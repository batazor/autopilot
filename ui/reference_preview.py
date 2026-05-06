"""Preview the latest reference screenshot from disk (no Redis)."""

from __future__ import annotations

from pathlib import Path

from config.reference_naming import (
    TEMPORAL_SUBDIR,
    reference_file_basename,
    rolling_preview_basename,
)


def references_root() -> Path:
    return Path(__file__).resolve().parent.parent / "references"


def list_reference_pngs(
    limit: int = 200,
    *,
    exclude_temporal: bool = False,
    exclude_crop: bool = False,
) -> list[Path]:
    """Newest-first PNG files under ``references/`` (recursive: ``**/*.png``).

    When ``exclude_temporal`` is True, omit everything under ``references/temporal/`` (rolling OCR preview).
    When ``exclude_crop`` is True, omit everything under ``references/crop/`` (exported bbox tiles, not full refs).
    """
    root = references_root()
    if not root.is_dir():
        return []
    files = sorted(root.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if exclude_temporal:
        files = [p for p in files if not _is_under_temporal(root, p)]
    if exclude_crop:
        files = [p for p in files if not _is_under_crop(root, p)]
    return files[:limit]


def _is_under_temporal(root: Path, p: Path) -> bool:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return False
    return len(rel.parts) > 0 and rel.parts[0] == TEMPORAL_SUBDIR


def _is_under_crop(root: Path, p: Path) -> bool:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return False
    return len(rel.parts) > 0 and rel.parts[0] == "crop"


def _newest_png_for_instance_then_any(root: Path, instance_id: str) -> Path | None:
    """Prefer newest ``{instance_id}_*.png`` anywhere under ``root``, else newest ``*.png``."""
    all_png = sorted(root.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    matches = [p for p in all_png if p.name.startswith(f"{instance_id}_")]
    if matches:
        return matches[0]
    return all_png[0] if all_png else None


def load_reference_preview(instance_id: str, name_input: str) -> tuple[bytes | None, str]:
    """Load PNG: basename if file exists; else auto-pick newest for instance, then newest overall."""
    root = references_root()
    root.mkdir(parents=True, exist_ok=True)

    def _rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    if name_input.strip():
        base = reference_file_basename(name_input.strip(), instance_id)
        if base == rolling_preview_basename(instance_id):
            path = root / TEMPORAL_SUBDIR / f"{base}.png"
        else:
            path = root / f"{base}.png"
        if path.is_file():
            return path.read_bytes(), _rel(path)
        # Basename did not match a file — fall through to automatic PNG.
    pick = _newest_png_for_instance_then_any(root, instance_id)
    if pick is not None:
        return pick.read_bytes(), _rel(pick)
    return None, ""


def resolve_rename_source_path(
    instance_id: str,
    name_input: str,
    picked_filename: str | None,
) -> Path | None:
    """
    Which existing PNG to rename: explicit pick from list, then basename match, else newest ``{instance_id}_*.png``.
    """
    root = references_root()
    if picked_filename and not picked_filename.startswith("("):
        p = root / picked_filename
        if p.is_file():
            return p
    if name_input.strip():
        base = reference_file_basename(name_input.strip(), instance_id)
        if base == rolling_preview_basename(instance_id):
            p = root / TEMPORAL_SUBDIR / f"{base}.png"
        else:
            p = root / f"{base}.png"
        if p.is_file():
            return p
    return _newest_png_for_instance_then_any(root, instance_id)


def rename_reference_to_basename(
    src: Path,
    name_input: str,
    instance_id: str,
) -> tuple[bool, str]:
    """Rename ``src`` to sanitized ``name_input``.png. Fails if target exists (other than ``src``)."""
    raw = name_input.strip()
    if not raw:
        return False, "Enter a basename first."
    root = references_root()
    dest_base = reference_file_basename(raw, instance_id)
    dest = (root / f"{dest_base}.png").resolve()
    src = src.resolve()
    if not src.is_file():
        return False, f"Source missing: {src.name}"
    if src == dest:
        return True, f"Already `{dest.name}`."
    if dest.is_file():
        return False, f"Target already exists: `{dest.name}` — remove it or choose another name."
    try:
        src.rename(dest)
    except OSError as exc:
        return False, str(exc)
    return True, f"Renamed to `{dest.name}`."
