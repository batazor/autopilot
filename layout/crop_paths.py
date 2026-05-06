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
    return repo_root / "references" / "crop" / f"{stem}_{label}.png"
