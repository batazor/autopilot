"""ADB screenshot into references/ for Streamlit (show immediately in the UI)."""

from __future__ import annotations

from pathlib import Path

from capture.adb_screencap import DEFAULT_ADB_BIN, adb_screencap_to_file
from config.loader import InstanceConfig
from config.reference_naming import reference_file_basename, reference_png_abs_path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def capture_reference_adb(
    inst: InstanceConfig,
    name_input: str,
    *,
    adb_bin: str = DEFAULT_ADB_BIN,
) -> tuple[bytes | None, str, str]:
    """
    Writes under ``references/`` (rolling preview under ``references/temporal/``).

    Returns (png bytes, path relative to ``references/``, error message). On success, error is ``""``.
    """
    rr = repo_root()
    serial = inst.bluestacks_window_title
    base = reference_file_basename(name_input if name_input.strip() else None, inst.instance_id)
    path = reference_png_abs_path(rr, base, inst.instance_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = adb_screencap_to_file(path, adb_bin=adb_bin.strip() or DEFAULT_ADB_BIN, serial=serial)
    if not ok:
        rel = path.relative_to(rr / "references").as_posix()
        return None, rel, msg
    rel = path.relative_to(rr / "references").as_posix()
    return path.read_bytes(), rel, ""


def capture_rolling_live_preview_adb(
    inst: InstanceConfig,
    *,
    adb_bin: str = DEFAULT_ADB_BIN,
) -> tuple[bytes | None, str, str]:
    """ADB capture into ``references/temporal/{instance_id}_current_state.png`` (same path as the worker)."""
    return capture_reference_adb(inst, "", adb_bin=adb_bin)
