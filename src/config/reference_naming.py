"""Shared naming rules for references/*.png (worker and UI preview)."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_REF_NAME_MAX = 120
_DEFAULT_BASE_SUFFIX = "current_state"

# Rolling OCR / UI preview captures (not shown in Labeling tree).
TEMPORAL_SUBDIR = "temporal"

# Event icons (rendered next to scenario names in the UI).
EVENTS_SUBDIR = "events"


def event_icon_abs_path(repo_root: Path, slug: str) -> Path | None:
    """Resolve a scenario ``icon:`` slug to its module-local logo PNG.

    Lookup order (first hit wins):
      1. ``modules/events/<slug>/references/logo.png`` — current convention.
      2. ``modules/events/<slug>/references/event.<slug>.png`` — legacy
         per-module naming kept by older event modules (bear_hunt, trials…).
      3. ``references/events/event.<slug>.png`` — pre-migration root cache.

    Returns ``None`` if none of the candidates exist. The slug is matched
    verbatim (no sanitisation) so a typo just yields a missing icon in the
    UI rather than a fallback collision.
    """
    s = str(slug or "").strip()
    if not s:
        return None
    candidates = (
        repo_root / "modules" / "events" / s / "references" / "logo.png",
        repo_root / "modules" / "events" / s / "references" / f"event.{s}.png",
        repo_root / "references" / EVENTS_SUBDIR / f"event.{s}.png",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def rolling_preview_basename(instance_id: str) -> str:
    """Basename (no .png) for the live ADB preview file."""
    return f"{instance_id}_{_DEFAULT_BASE_SUFFIX}"


def reference_png_abs_path(repo_root: Path, base: str, instance_id: str) -> Path:
    """Path for ``references/temporal/<base>.png`` (preview) or ``references/<base>.png``."""
    refs = repo_root / "references"
    if base == rolling_preview_basename(instance_id):
        out = refs / TEMPORAL_SUBDIR / f"{base}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    return refs / f"{base}.png"


def temporal_png_abs_path(repo_root: Path, base: str) -> Path:
    """Path for a temporary PNG under ``references/temporal/<base>.png`` (non-rolling)."""
    return temporal_png_abs_path_in_refs(repo_root / "references", base)


def temporal_png_abs_path_in_refs(references_dir: Path, base: str) -> Path:
    """Path for a temporary PNG under ``<references_dir>/temporal/<base>.png``."""
    refs = references_dir / TEMPORAL_SUBDIR
    out = refs / f"{base}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def is_preview_snapshot_stem(stem: str, instance_id: str) -> bool:
    """True for the rolling preview file stem ``{instance_id}_current_state``."""
    return stem == f"{instance_id}_{_DEFAULT_BASE_SUFFIX}"


def unique_label_capture_basename(instance_id: str) -> str:
    """Basename for a new Labeling capture under ``references/`` (not rolling preview)."""
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return reference_file_basename(f"{instance_id}_shot_{stamp}_{short}", instance_id)


def reference_file_basename(raw: str | None, instance_id: str) -> str:
    """Safe basename without .png extension.

    Empty input uses ``{instance_id}_current_state`` (rolling preview; repeated captures overwrite).
    """
    default = rolling_preview_basename(instance_id)
    if raw is None or not str(raw).strip():
        return default
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(raw).strip())
    s = s.strip("._-")
    if not s:
        return default
    return s[:_REF_NAME_MAX]
