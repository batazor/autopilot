"""Shared helpers for the Labeling page (tree badges, workflow, delete impact)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.reference_naming import TEMPORAL_SUBDIR, reference_file_basename
from dashboard.reference_ocr_paths import reference_basename_stem, resolve_ocr_path_in_reference_context


@dataclass(frozen=True)
class ReferenceLeafMeta:
    rel: str
    screen_id: str
    region_count: int
    active_version: str | None
    unassigned: bool


def _ocr_to_ref_rel(ocr: str, ref_root: Path) -> str | None:
    ocr = str(ocr or "").replace("\\", "/").strip()
    if not ocr:
        return None
    p = Path(ocr)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(ref_root.resolve()).as_posix()
        except OSError:
            return None
    try:
        rel = p.relative_to("references")
    except ValueError:
        rel = p
    if rel.suffix.lower() != ".png":
        return None
    return rel.as_posix()


def _count_regions(entry: dict[str, Any]) -> int:
    names: set[str] = set()
    for reg in entry.get("regions") or []:
        if isinstance(reg, dict):
            nm = str(reg.get("name") or "").strip()
            if nm:
                names.add(nm)
    for ver in entry.get("versions") or []:
        if not isinstance(ver, dict):
            continue
        for reg in ver.get("regions") or []:
            if isinstance(reg, dict):
                nm = str(reg.get("name") or "").strip()
                if nm:
                    names.add(nm)
    return len(names)


def build_reference_leaf_meta_index(
    area_doc: dict[str, Any] | None,
    ref_root: Path,
    *,
    unassigned_title: str = "(unassigned)",
) -> dict[str, ReferenceLeafMeta]:
    """Map ``references/``-relative PNG path → labeling metadata for tree titles."""

    by_rel: dict[str, ReferenceLeafMeta] = {}
    if not isinstance(area_doc, dict):
        return by_rel
    for entry in area_doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        rel = _ocr_to_ref_rel(str(entry.get("ocr") or ""), ref_root)
        if not rel:
            continue
        sid = str(entry.get("screen_id") or "").strip()
        av = entry.get("active_version")
        active_ver = str(av).strip() if av is not None and str(av).strip() else None
        by_rel[rel] = ReferenceLeafMeta(
            rel=rel,
            screen_id=sid,
            region_count=_count_regions(entry),
            active_version=active_ver,
            unassigned=not bool(sid),
        )
    # PNGs with no area entry are unassigned when we see them from the file list.
    return by_rel


def format_reference_leaf_title(
    rel: str,
    meta: ReferenceLeafMeta | None,
    *,
    unassigned_title: str = "(unassigned)",
) -> str:
    """Human title for ``st_ant_tree`` leaf nodes."""
    base = Path(rel).name
    if meta is None:
        return f"⚠ {base} · no area.json"
    parts = [base]
    if meta.region_count:
        parts.append(f"{meta.region_count} reg")
    if meta.active_version:
        parts.append(f"v:{meta.active_version}")
    if meta.unassigned:
        parts[0] = f"⚠ {base}"
    return " · ".join(parts)


def format_screen_id_group_title(
    sid: str,
    file_count: int,
    *,
    unassigned_title: str = "(unassigned)",
) -> str:
    if sid == unassigned_title:
        return f"⚠ {sid} · {file_count} ref(s)"
    return f"{sid} · {file_count} ref(s)"


def suggest_basename_from_entry(
    entry: dict[str, Any] | None,
    instance_id: str,
    *,
    version_suffix: bool = True,
) -> str | None:
    """Propose a stable basename from ``screen_id`` (+ optional active version)."""
    if not isinstance(entry, dict):
        return None
    sid = str(entry.get("screen_id") or "").strip()
    if not sid:
        return None
    slug = sid.replace(".", "_")
    inst = str(instance_id or "").strip()
    raw = f"{inst}_{slug}" if inst else slug
    av = entry.get("active_version")
    ver = str(av).strip() if av is not None else ""
    if version_suffix and ver and ver not in ("default", ""):
        raw = f"{raw}_{ver}"
    return reference_file_basename(raw, instance_id)


@dataclass(frozen=True)
class LabelingWorkflowStep:
    key: str
    label: str
    done: bool
    detail: str = ""


def labeling_workflow_steps(
    *,
    pending_rel: str | None,
    sel_rel: str | None,
    entry: dict[str, Any] | None,
    region_count: int,
    area_saved: bool,
) -> list[LabelingWorkflowStep]:
    """Ordered checklist for capture → publish → annotate."""
    temporal = bool(
        pending_rel
        and (
            pending_rel == TEMPORAL_SUBDIR
            or pending_rel.startswith(f"{TEMPORAL_SUBDIR}/")
        )
    )
    has_png = bool(sel_rel)
    published = has_png and not temporal
    has_regions = region_count > 0
    return [
        LabelingWorkflowStep(
            "capture",
            "Screenshot",
            done=has_png,
            detail="temporal (unsaved)" if temporal else ("on disk" if published else ""),
        ),
        LabelingWorkflowStep(
            "publish",
            "Basename / publish",
            done=published,
            detail="assign basename to move out of temporal/" if temporal else "",
        ),
        LabelingWorkflowStep(
            "screen",
            "Screen ID",
            done=bool(entry and str(entry.get("screen_id") or "").strip()),
            detail=str(entry.get("screen_id") or "").strip() if entry else "",
        ),
        LabelingWorkflowStep(
            "regions",
            "Regions",
            done=has_regions,
            detail=f"{region_count} region(s)" if has_regions else "draw or add regions",
        ),
        LabelingWorkflowStep(
            "save",
            "area.json saved",
            done=area_saved and published,
            detail="use **Save area.json** in the canvas column" if published else "",
        ),
    ]


@dataclass(frozen=True)
class DeleteReferenceImpact:
    rel: str
    area_entries: int
    region_names: tuple[str, ...]
    crop_count: int


def preview_delete_reference_impact(
    repo_root: Path,
    ref_root: Path,
    rel_posix: str,
    area_doc: dict[str, Any] | None,
    *,
    references_prefix: str = "references",
) -> DeleteReferenceImpact:
    """Count ``area.json`` rows and crop tiles that would be removed with a reference PNG."""
    rel_posix = rel_posix.replace("\\", "/").strip()
    try:
        target = (ref_root / rel_posix).resolve()
    except OSError:
        return DeleteReferenceImpact(rel=rel_posix, area_entries=0, region_names=(), crop_count=0)

    region_names: list[str] = []
    matching = 0
    if isinstance(area_doc, dict):
        for entry in area_doc.get("screens") or []:
            if not isinstance(entry, dict):
                continue
            ocr_raw = str(entry.get("ocr") or "").strip()
            if not ocr_raw:
                continue
            try:
                if (
                    resolve_ocr_path_in_reference_context(
                        ocr_raw, references_prefix, repo_root_path=repo_root
                    )
                    != target
                ):
                    continue
            except OSError:
                continue
            matching += 1
            for reg in entry.get("regions") or []:
                if isinstance(reg, dict):
                    nm = str(reg.get("name") or "").strip()
                    if nm:
                        region_names.append(nm)
            for ver in entry.get("versions") or []:
                if not isinstance(ver, dict):
                    continue
                for reg in ver.get("regions") or []:
                    if isinstance(reg, dict):
                        nm = str(reg.get("name") or "").strip()
                        if nm and nm not in region_names:
                            region_names.append(nm)

    crop_count = 0
    crop_dir = ref_root / "crop"
    if crop_dir.is_dir():
        stem = reference_basename_stem(rel_posix)
        prefix = f"{stem}_"
        for cp in crop_dir.glob(f"{prefix}*.png"):
            if cp.is_file():
                crop_count += 1

    return DeleteReferenceImpact(
        rel=rel_posix,
        area_entries=matching,
        region_names=tuple(sorted(set(region_names))),
        crop_count=crop_count,
    )
