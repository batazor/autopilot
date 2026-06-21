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

# Per-module Labeling subdir for pending captures (e.g. ``modules/<id>/references/temporal/``).
TEMPORAL_SUBDIR = "temporal"

# Top-level directory for live ADB rolling/approval previews. Hosted outside
# ``references/`` so the Labeling references tree stays free of instance state.
INSTANCE_PREVIEW_DIR = "temporal"

# Event icons (rendered next to scenario names in the UI).
EVENTS_SUBDIR = "events"

# Dreamscape solver scene guides (community map images, not labeling refs).
MAPS_SUBDIR = "maps"


def instance_preview_root(repo_root: Path) -> Path:
    """Top-level directory for ADB rolling/approval previews (``<repo>/temporal``)."""
    return repo_root / INSTANCE_PREVIEW_DIR


def event_icon_abs_path(repo_root: Path, slug: str) -> Path | None:
    """Resolve a scenario ``icon:`` slug to its module-local logo PNG.

    Lookup order (first hit wins):
      1. ``modules/events/<slug>/references/logo.png`` — current convention.
      2. ``modules/events/<slug>/references/event.<slug>.png`` — legacy
         per-module naming kept by older event modules (bear_hunt, trials…).

    Returns ``None`` if neither file exists. The slug is matched verbatim
    (no sanitisation) so a typo just yields a missing icon in the UI rather
    than a fallback collision.
    """
    s = str(slug or "").strip()
    if not s:
        return None
    from config.games import default_game, modules_root_for

    events_dir = modules_root_for(default_game(), repo_root=repo_root) / "events"
    candidates = (
        events_dir / s / "references" / "logo.png",
        events_dir / s / "references" / f"event.{s}.png",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def rolling_preview_basename(instance_id: str) -> str:
    """Basename (no .png) for the live ADB preview file."""
    return f"{instance_id}_{_DEFAULT_BASE_SUFFIX}"


def temporal_png_abs_path(repo_root: Path, base: str) -> Path:
    """Path for an instance-level rolling/approval preview (``<repo>/temporal/<base>.png``)."""
    out = instance_preview_root(repo_root) / f"{base}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


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
