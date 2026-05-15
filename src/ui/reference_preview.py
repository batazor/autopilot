"""Preview the latest reference screenshot from disk (no Redis)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from config.paths import repo_root
from config.reference_naming import (
    TEMPORAL_SUBDIR,
    reference_file_basename,
    rolling_preview_basename,
)

# UI capture buttons (labeling "Take screenshot", area annotator "Capture",
# operator-on-demand "fetch screenshot") all need a fresh frame. Rather than
# issuing their own ADB screencap, they copy the rolling-loop's most recent
# PNG. This caps "fresh" at the rolling interval (1 s typical) — anything older
# than this threshold means the worker isn't producing frames.
ROLLING_PREVIEW_STALE_AFTER_SECONDS: float = 10.0


def references_root() -> Path:
    return repo_root() / "references"


def list_reference_pngs(
    limit: int = 200,
    *,
    root: Path | None = None,
    exclude_temporal: bool = False,
    exclude_crop: bool = False,
) -> list[Path]:
    """Newest-first PNG files under a references directory (recursive: ``**/*.png``).

    When ``exclude_temporal`` is True, omit everything under ``<root>/temporal/`` (rolling OCR preview).
    When ``exclude_crop`` is True, omit everything under ``<root>/crop/`` (exported bbox tiles, not full refs).
    """
    root = (root or references_root()).resolve()
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


def rolling_live_preview_path(instance_id: str) -> Path:
    """Worker + Instance rolling frame: ``references/temporal/{instance_id}_current_state.png``."""
    return references_root() / TEMPORAL_SUBDIR / f"{rolling_preview_basename(instance_id)}.png"


def copy_rolling_preview_to(
    instance_id: str,
    target: Path,
    *,
    stale_after_seconds: float = ROLLING_PREVIEW_STALE_AFTER_SECONDS,
) -> tuple[bool, str]:
    """Copy the rolling preview PNG to ``target``.

    Returns ``(ok, msg)``. ``msg`` is the error description on failure and
    ``""`` on success.

    The rolling loop is the only ADB capture path in the system; UI buttons
    that previously issued their own ``adb_screencap_to_file`` now read this
    file. Missing or stale (> ``stale_after_seconds``) means the worker isn't
    publishing frames — return a descriptive error so the UI can surface it.
    """
    src = rolling_live_preview_path(instance_id)
    if not src.is_file():
        return False, (
            f"no rolling preview PNG yet for {instance_id!r} — "
            "start the worker (`uv run wos`) so it captures frames"
        )
    age = time.time() - src.stat().st_mtime
    if age > stale_after_seconds:
        return False, (
            f"rolling preview for {instance_id!r} is ~{age:.0f}s old — "
            "worker isn't refreshing (check ADB / emulator)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, target)
    except OSError as e:
        return False, f"copy rolling preview to {target}: {e}"
    return True, ""


def load_rolling_instance_preview(instance_id: str) -> tuple[bytes | None, str, float | None]:
    """Load the live ADB rolling PNG for this instance (mtime = disk ``st_mtime``)."""
    root = references_root()
    root.mkdir(parents=True, exist_ok=True)
    path = rolling_live_preview_path(instance_id)
    if path.is_file():
        rel = path.relative_to(root).as_posix()
        return path.read_bytes(), rel, path.stat().st_mtime
    return None, "", None


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
    *,
    references_dir: Path | None = None,
) -> tuple[bool, str]:
    """Rename ``src`` to sanitized ``name_input``.png. Fails if target exists (other than ``src``)."""
    raw = name_input.strip()
    if not raw:
        return False, "Enter a basename first."
    root = (references_dir or references_root()).resolve()
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


def move_temporal_to_reference_basename(
    *,
    src_temporal: Path,
    name_input: str,
    instance_id: str,
    references_dir: Path | None = None,
) -> tuple[bool, str, str | None]:
    """Move a pending PNG from ``<refs>/temporal/`` to ``<refs>/<basename>.png``.

    Returns ``(ok, message, new_rel_under_references_or_none)``.
    """
    raw = name_input.strip()
    if not raw:
        return False, "Enter a basename first.", None

    root = (references_dir or references_root()).resolve()
    src = src_temporal.resolve()
    if not src.is_file():
        return False, f"Source missing: `{src.name}`.", None

    try:
        rel = src.relative_to(root)
    except ValueError:
        return False, "Invalid source path.", None

    if len(rel.parts) == 0 or rel.parts[0] != TEMPORAL_SUBDIR:
        return False, "Source must be under `references/temporal/`.", None

    dest_base = reference_file_basename(raw, instance_id)
    dest = (root / f"{dest_base}.png").resolve()
    if dest.is_file():
        return False, f"Target already exists: `{dest.name}` — remove it or choose another name.", None

    try:
        src.rename(dest)
    except OSError as exc:
        return False, str(exc), None

    return True, f"Saved as `{dest.name}`.", f"{dest_base}.png"
