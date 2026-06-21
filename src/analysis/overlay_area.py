"""Default area manifest for overlay analysis (worker + startup validation)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def default_area_doc_for_overlay(repo_root: Path) -> dict[str, Any]:
    """Merged core ``area.json`` + ``modules/*/area.yaml``.

    ``run_overlay_analysis`` uses this when ``area_doc`` is omitted. Startup
    validation must use the same helper so module regions (e.g.
    ``myriad_bazaar.title``) cannot drift from the runtime overlay path.
    """
    from layout.area_manifest import load_area_doc

    return load_area_doc(repo_root)
