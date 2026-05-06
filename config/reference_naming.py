"""Shared naming rules for references/*.png (worker and UI preview)."""

from __future__ import annotations

import re
from pathlib import Path

_REF_NAME_MAX = 120
_DEFAULT_BASE_SUFFIX = "current_state"

# Rolling OCR / UI preview captures (not shown in Labeling tree).
TEMPORAL_SUBDIR = "temporal"


def rolling_preview_basename(instance_id: str) -> str:
    """Basename (no .png) for the live ADB preview file."""
    return f"{instance_id}_{_DEFAULT_BASE_SUFFIX}"


def reference_png_abs_path(repo_root: Path, base: str, instance_id: str) -> Path:
    """Absolute path under repo: rolling preview → references/temporal/<base>.png, else references/<base>.png."""
    refs = repo_root / "references"
    if base == rolling_preview_basename(instance_id):
        out = refs / TEMPORAL_SUBDIR / f"{base}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    return refs / f"{base}.png"


def is_preview_snapshot_stem(stem: str, instance_id: str) -> bool:
    """True for the rolling preview file stem ``{instance_id}_current_state``."""
    return stem == f"{instance_id}_{_DEFAULT_BASE_SUFFIX}"


def reference_file_basename(raw: str | None, instance_id: str) -> str:
    """Safe basename without .png extension.

    Empty input uses ``{instance_id}_current_state`` so repeated captures overwrite one rolling preview file per instance.
    """
    default = rolling_preview_basename(instance_id)
    if raw is None or not str(raw).strip():
        return default
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(raw).strip())
    s = s.strip("._-")
    if not s:
        return default
    return s[:_REF_NAME_MAX]
