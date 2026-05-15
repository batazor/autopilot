"""Resolve which ``references/…`` PNG refresh should overwrite (version-aware)."""
from __future__ import annotations

from pathlib import Path


def ocr_path_to_ref_rel(ocr: str) -> str | None:
    raw = str(ocr or "").replace("\\", "/").strip()
    if not raw:
        return None
    p = Path(raw)
    try:
        return p.relative_to("references").as_posix()
    except ValueError:
        return None


def resolve_labeling_refresh_target_rel(
    tree_sel: str,
    *,
    entry_default_ref_rel: str | None,
    active_version_ref_rel: str | None,
    temporal_subdir: str,
) -> tuple[str, str | None]:
    """Return ``(path_under_references/, optional markdown note)``."""

    rel_disp = str(tree_sel).replace("\\", "/").strip()
    ts = temporal_subdir.replace("\\", "/").strip().rstrip("/")
    if rel_disp == ts or rel_disp.startswith(f"{ts}/"):
        return rel_disp, None
    if (
        entry_default_ref_rel
        and entry_default_ref_rel == rel_disp
        and active_version_ref_rel
        and active_version_ref_rel != rel_disp
    ):
        vr = active_version_ref_rel
        return vr, (
            f"Active editing version → **`references/{vr}`** "
            f"(tree selection **`references/{rel_disp}`**)."
        )
    return rel_disp, None
