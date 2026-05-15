"""Paths for PNG tiles exported from labeling (``references/crop/<ref_stem>_<region>.png``)."""
from __future__ import annotations

import re
from pathlib import Path


def safe_crop_filename_part(name: str, fallback: str = "region") -> str:
    raw = (name or "").strip() or fallback
    out = re.sub(r"[^\w\-.]+", "_", raw)
    out = out.strip("._-") or "region"
    return out[:120]


def exported_crop_png(repo_root: Path, reference_repo_rel: str, region_name: str) -> Path:
    """Path for the crop file produced by ``export_region_crops`` / Labeling **Write crops**."""

    stem = Path(reference_repo_rel).stem
    label = safe_crop_filename_part(region_name)
    filename = f"{stem}_{label}.png"
    ref_parts = Path(reference_repo_rel).parts
    if len(ref_parts) >= 3 and ref_parts[0] == "modules":
        module_crop = repo_root / ref_parts[0] / ref_parts[1] / "references" / "crop" / filename
        if module_crop.is_file():
            return module_crop
        return module_crop
    return repo_root / "references" / "crop" / filename


def resolve_reference_path(repo_root: Path, reference_repo_rel: str) -> Path:
    """Resolve a repository-relative reference screenshot path."""
    return repo_root / reference_repo_rel
