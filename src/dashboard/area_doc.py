"""Pure ``area.json`` / ``area.yaml`` helpers (no Streamlit deps).

Extracted from the deleted ``src/ui/area_annotator.py`` so the API server,
worker, and tests can read/write ``area.json``, list screen ids, and export
template crops without dragging Streamlit into the import graph.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

import cv2
import numpy as np
import yaml
from PIL import Image

from config.paths import repo_root
from dashboard.reference_ocr_paths import (
    module_local_ocr_for_reference_path as _module_local_ocr_for_reference_path,
)
from dashboard.reference_ocr_paths import (
    resolve_ocr_path_in_reference_context as _resolve_ocr_path_in_reference_context,
)
from layout.area_regions import (
    dedupe_redundant_version_regions,
    get_version_block,
    is_auxiliary_overlay_region,
    region_names_for,
    validate_unique_region_names,
    validate_versions,
)
from layout.crop_paths import exported_crop_png

if TYPE_CHECKING:
    from collections.abc import Callable


class BBoxDict(TypedDict):
    x: float
    y: float
    width: float
    height: float
    rotation: float
    original_width: int
    original_height: int


class RegionDict(TypedDict, total=False):
    name: str
    aliases: list[str]
    action: str
    type: str
    threshold: float
    bbox: BBoxDict
    overlay_auxiliary: bool
    has_red_dot: bool
    isSearch: bool
    tap_hold_ms: int


class VersionDict(TypedDict, total=False):
    id: str
    cond: str
    ocr: str
    regions: list[RegionDict]
    removed: list[str]


class AreaEntryDict(TypedDict, total=False):
    id: int
    ocr: str
    screen_id: str
    screen_region: str
    regions: list[RegionDict]
    versions: list[VersionDict]


class AreaDocDict(TypedDict, total=False):
    version: int
    screens: list[AreaEntryDict]


REPO_ROOT = repo_root()
REFERENCES_DIR = REPO_ROOT / "references"


def crop_region(
    image: Image.Image,
    left: float,
    top: float,
    width: float,
    height: float,
) -> Image.Image:
    """Crop ``image`` using pixel coordinates (same space as canvas / resized background)."""
    L = int(math.floor(left))
    T = int(math.floor(top))
    R = int(math.ceil(left + width))
    B = int(math.ceil(top + height))
    W, Ht = image.size
    L = max(0, min(L, W - 1))
    T = max(0, min(T, Ht - 1))
    R = max(L + 1, min(R, W))
    B = max(T + 1, min(B, Ht))
    return image.crop((L, T, R, B))


def _safe_crop_filename_part(name: str, fallback: str) -> str:
    raw = (name or "").strip() or fallback
    out = re.sub(r"[^\w\-.]+", "_", raw)
    out = out.strip("._-") or "region"
    return out[:120]


def crop_path_for_entry_region(
    repo_root: Path,
    entry: AreaEntryDict | None,
    region_name: str,
    *,
    active_version: str | None = None,
) -> Path | None:
    """Return the on-disk crop file for ``region_name`` within ``entry``."""
    if not isinstance(entry, dict):
        return None
    name = (region_name or "").strip()
    if not name:
        return None

    if active_version:
        ver_block = get_version_block(cast("dict[str, Any]", entry), active_version)
        if ver_block is not None:
            for reg in ver_block.get("regions") or []:
                if isinstance(reg, dict) and str(reg.get("name", "") or "").strip() == name:
                    chosen_ocr = str(ver_block.get("ocr", "") or "").strip() or str(
                        entry.get("ocr") or ""
                    ).strip()
                    return exported_crop_png(repo_root, chosen_ocr, name) if chosen_ocr else None

    for reg in entry.get("regions") or []:
        if isinstance(reg, dict) and str(reg.get("name", "") or "").strip() == name:
            chosen_ocr = str(entry.get("ocr") or "").strip()
            return exported_crop_png(repo_root, chosen_ocr, name) if chosen_ocr else None

    return None


def _count_exportable_crop_regions(regions: list[RegionDict]) -> int:
    return sum(
        1
        for r in regions
        if r.get("bbox") and not r.get("overlay_auxiliary")
    )


def export_region_crops(
    pil_original: Image.Image,
    reference_repo_rel: str,
    regions: list[RegionDict],
    *,
    repo_root: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """Write a crop PNG for each region with a bbox."""
    root = repo_root or REPO_ROOT
    if not Path(reference_repo_rel).stem:
        msg = "Invalid reference path for crop export."
        raise ValueError(msg)
    ow, oh = pil_original.size
    indexed = [
        (i, r)
        for i, r in enumerate(regions)
        if r.get("bbox") and not r.get("overlay_auxiliary")
    ]
    total = len(indexed)
    written: list[Path] = []
    if progress is not None and total > 0:
        progress(0.0)
    for step, (i, reg) in enumerate(indexed):
        bbox = reg.get("bbox") or {}
        region_name = str(reg.get("name", "")) or f"region_{i}"
        left = bbox["x"] / 100.0 * ow
        top = bbox["y"] / 100.0 * oh
        w = bbox["width"] / 100.0 * ow
        h = bbox["height"] / 100.0 * oh
        tile = crop_region(pil_original, left, top, w, h)
        dest = exported_crop_png(root, reference_repo_rel, region_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tile.save(dest, format="PNG")
        written.append(dest)
        if progress is not None and total > 0:
            progress(min(1.0, (step + 1) / total))
    return written


def find_stale_crops(
    doc: AreaDocDict,
    *,
    repo_root: Path | None = None,
    tolerance_px: int = 2,
) -> list[dict[str, Any]]:
    """Return crops whose on-disk dimensions disagree with their bbox."""
    root = repo_root or REPO_ROOT
    stale: list[dict[str, Any]] = []
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue

        tasks: list[tuple[str, list[RegionDict]]] = []
        default_ocr = str(entry.get("ocr") or "").strip()
        base_regions_raw = entry.get("regions")
        if default_ocr and isinstance(base_regions_raw, list):
            tasks.append((default_ocr, [r for r in base_regions_raw if isinstance(r, dict)]))
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            vid = str(ver.get("id", "") or "").strip()
            if not vid:
                continue
            ver_ocr = str(ver.get("ocr", "") or "").strip() or default_ocr
            ver_regions = ver.get("regions")
            if ver_ocr and isinstance(ver_regions, list):
                tasks.append((ver_ocr, [r for r in ver_regions if isinstance(r, dict)]))

        for ocr_rel, regions in tasks:
            ref_abs = root / ocr_rel
            if not ref_abs.is_file():
                continue
            try:
                with Image.open(ref_abs) as pil:
                    ow, oh = pil.size
            except OSError:
                continue
            for reg in regions:
                if reg.get("overlay_auxiliary"):
                    continue
                bbox = reg.get("bbox")
                if not isinstance(bbox, dict):
                    continue
                name = str(reg.get("name", "")).strip()
                if not name:
                    continue
                w = bbox["width"] / 100.0 * ow
                h = bbox["height"] / 100.0 * oh
                expected_w = max(1, int(round(w)))
                expected_h = max(1, int(round(h)))
                crop_path = exported_crop_png(root, ocr_rel, name)
                if not crop_path.is_file():
                    continue
                try:
                    with Image.open(crop_path) as cp:
                        actual_w, actual_h = cp.size
                except OSError:
                    continue
                if (
                    abs(actual_w - expected_w) > tolerance_px
                    or abs(actual_h - expected_h) > tolerance_px
                ):
                    stale.append(
                        {
                            "ocr": ocr_rel,
                            "region": name,
                            "expected_w": expected_w,
                            "expected_h": expected_h,
                            "actual_w": actual_w,
                            "actual_h": actual_h,
                            "crop_path": crop_path,
                        }
                    )
    return stale


def export_all_region_crops_for_area_doc(
    doc: AreaDocDict,
    *,
    repo_root: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> tuple[list[Path], list[str]]:
    """Write template crops for every screen whose ``ocr`` PNG exists on disk."""
    root = repo_root or REPO_ROOT
    written: list[Path] = []
    warnings: list[str] = []
    screens = doc.get("screens") or []

    tasks: list[tuple[str, list[RegionDict], Path]] = []
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        default_ocr = str(entry.get("ocr") or "").strip()

        base_regions_raw = entry.get("regions")
        base_regions: list[RegionDict] = (
            cast(
                "list[RegionDict]",
                [r for r in base_regions_raw if isinstance(r, dict)],
            )
            if isinstance(base_regions_raw, list)
            else []
        )
        if default_ocr and base_regions:
            rel = Path(default_ocr)
            abs_path = rel if rel.is_absolute() else (root / rel)
            if not abs_path.is_file():
                warnings.append(f"Skip (missing file): `{default_ocr}`")
            elif _count_exportable_crop_regions(base_regions) > 0:
                tasks.append((default_ocr, base_regions, abs_path))

        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            vid = str(ver.get("id", "") or "").strip()
            if not vid:
                continue
            ver_regions_raw = ver.get("regions")
            ver_regions: list[RegionDict] = (
                cast(
                    "list[RegionDict]",
                    [r for r in ver_regions_raw if isinstance(r, dict)],
                )
                if isinstance(ver_regions_raw, list)
                else []
            )
            if not ver_regions or _count_exportable_crop_regions(ver_regions) == 0:
                continue
            ver_ocr = str(ver.get("ocr", "") or "").strip() or default_ocr
            if not ver_ocr:
                warnings.append(
                    f"Skip version `{vid}`: no reference image (neither version `ocr` nor entry `ocr`)"
                )
                continue
            rel = Path(ver_ocr)
            abs_path = rel if rel.is_absolute() else (root / rel)
            if not abs_path.is_file():
                warnings.append(f"Skip (missing file): `{ver_ocr}`")
                continue
            tasks.append((ver_ocr, ver_regions, abs_path))

    total_files = sum(_count_exportable_crop_regions(regs) for _, regs, _ in tasks)
    done_files = 0

    for ocr_raw, regions, abs_path in tasks:
        n_this = _count_exportable_crop_regions(regions)
        try:
            pil = Image.open(abs_path)
            pil.load()
        except OSError as e:
            warnings.append(f"Could not open `{ocr_raw}`: {e}")
            done_files += n_this
            if progress is not None and total_files > 0:
                progress(min(1.0, done_files / total_files))
            continue

        def _prog_local(
            frac: float, *, _done: int = done_files, _n_this: int = n_this
        ) -> None:
            if progress is None:
                return
            if total_files <= 0:
                progress(1.0)
            else:
                progress(min(1.0, (_done + frac * _n_this) / total_files))

        try:
            outs = export_region_crops(
                pil,
                ocr_raw,
                regions,
                repo_root=root,
                progress=_prog_local,
            )
            written.extend(outs)
        except (OSError, ValueError) as e:
            warnings.append(f"`{ocr_raw}`: {e}")
        finally:
            done_files += n_this
            if progress is not None and total_files > 0:
                progress(min(1.0, done_files / total_files))

    if progress is not None and total_files == 0:
        progress(1.0)

    return written, warnings


def default_area_doc(screens: list[AreaEntryDict] | None = None) -> AreaDocDict:
    return AreaDocDict(version=2, screens=list(screens or []))


def normalize_area_file(raw: Any) -> AreaDocDict:
    """Accept legacy ``[ {...}, ... ]`` or ``{ "screens": [...] }`` (ignores removed ``fsm``)."""
    if isinstance(raw, list):
        return default_area_doc(raw)  # type: ignore[arg-type]

    if isinstance(raw, dict):
        screens = raw.get("screens")
        if not isinstance(screens, list):
            msg = "area.json object must include a 'screens' array"
            raise TypeError(msg)
        return AreaDocDict(
            version=int(raw.get("version", 2)),
            screens=screens,  # type: ignore[arg-type]
        )

    msg = "area.json must be a JSON array or an object with 'screens'"
    raise ValueError(msg)


def strip_exist_region_types(doc: dict[str, Any]) -> int:
    """Remove obsolete ``type`` from template-match regions."""
    removed = 0
    for screen in doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        region_groups: list[Any] = [screen.get("regions")]
        versions = screen.get("versions")
        if isinstance(versions, list):
            region_groups.extend(
                version.get("regions")
                for version in versions
                if isinstance(version, dict)
            )
        for regions in region_groups:
            if not isinstance(regions, list):
                continue
            for region in regions:
                if not isinstance(region, dict):
                    continue
                if str(region.get("action") or "").strip() == "exist" and "type" in region:
                    region.pop("type", None)
                    removed += 1
    return removed


def save_json(path: Path, doc: AreaDocDict) -> int:
    """Write ``area.json`` / module ``area.yaml`` (``version`` + ``screens``)."""
    doc_dict = cast("dict[str, Any]", doc)
    strip_exist_region_types(doc_dict)
    removed = dedupe_redundant_version_regions(doc_dict)
    validate_unique_region_names(doc_dict)
    validate_versions(doc_dict)
    if path.suffix.lower() in {".yaml", ".yml"}:
        content = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True)
    else:
        content = json.dumps(doc, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    Path(tmp).replace(path)
    return removed


def load_json(path: Path) -> AreaDocDict:
    if not path.exists():
        return default_area_doc([])
    raw_text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(raw_text)
    return normalize_area_file(raw)


def all_screen_ids(doc: AreaDocDict) -> list[str]:
    ids: set[str] = set()
    for s in doc.get("screens") or []:
        sid = str(s.get("screen_id", "") or "").strip()
        if sid:
            ids.add(sid)
    return sorted(ids)


def screen_id_select_options(doc: AreaDocDict, current_screen_id: str) -> list[str]:
    """Options for Screen ID: ``""`` = None; then sorted node ids from area + entries (always includes ``current``)."""
    ids: set[str] = set(all_screen_ids(doc))
    try:
        from navigation.screen_graph import screen_verify_screen_names

        ids.update(screen_verify_screen_names())
    except Exception:
        pass
    try:
        from navigation.screen_graph import EDGE_TAPS

        for a, b in EDGE_TAPS:
            if a:
                ids.add(str(a))
            if b:
                ids.add(str(b))
    except Exception:
        pass
    ids.update({"mail"})
    cur = (current_screen_id or "").strip()
    if cur:
        ids.add(cur)
    return ["", *sorted(x for x in ids if x)]


def _entry_region_names(entry: AreaEntryDict) -> list[str]:
    names: set[str] = set()
    for reg in entry.get("regions") or []:
        if isinstance(reg, dict):
            names.update(region_names_for(cast("dict[str, Any]", reg)))
    for version in entry.get("versions") or []:
        if not isinstance(version, dict):
            continue
        for reg in version.get("regions") or []:
            if isinstance(reg, dict):
                names.update(region_names_for(cast("dict[str, Any]", reg)))
    return sorted(names)


def _doc_with_repo_relative_ocr(
    doc: AreaDocDict, area_path: Path | None, repo_root: Path
) -> AreaDocDict:
    """Prefix module-local ``ocr`` fields with the module directory.

    Module ``area.yaml`` files store ``ocr`` relative to the module directory
    (``references/foo.png``). The crop-export helpers resolve those paths
    against ``repo_root`` directly — without this normalisation they would
    open the root ``references/foo.png`` (a different image entirely) and
    write crops under the root ``references/crop/`` instead of
    ``modules/<id>/references/crop/``.

    Idempotent: returns the original doc when ``area_path`` is ``None`` (the
    merged "All" scope already carries fully-normalized paths), when the path
    is not under ``modules/<id>/``, or when an entry's ``ocr`` already starts
    with ``modules/``.
    """
    from config.games import GAMES_DIR_NAME, is_known_game, modules_path_prefix

    if area_path is None:
        return doc
    try:
        rel = area_path.parent.relative_to(repo_root)
    except ValueError:
        return doc
    parts = rel.parts
    # Module area paths live at games/<game>/<module-id>/area.yaml — need at
    # least three segments and a known game in the second one.
    if len(parts) < 3 or parts[0] != GAMES_DIR_NAME or not is_known_game(parts[1]):
        return doc
    prefix = "/".join(parts)
    modules_prefix_for = modules_path_prefix(parts[1]) + "/"

    def _prefix(value: str) -> str:
        v = (value or "").strip()
        if not v or v.startswith(modules_prefix_for):
            return v
        return f"{prefix}/{v}"

    screens_in = doc.get("screens") or []
    new_screens: list[Any] = []
    for entry in screens_in:
        if not isinstance(entry, dict):
            new_screens.append(entry)
            continue
        entry_out = dict(entry)
        if entry.get("ocr"):
            entry_out["ocr"] = _prefix(str(entry["ocr"]))
        versions = entry.get("versions")
        if isinstance(versions, list):
            new_versions: list[Any] = []
            for ver in versions:
                if not isinstance(ver, dict):
                    new_versions.append(ver)
                    continue
                ver_out = dict(ver)
                if ver.get("ocr"):
                    ver_out["ocr"] = _prefix(str(ver["ocr"]))
                new_versions.append(ver_out)
            entry_out["versions"] = new_versions
        new_screens.append(entry_out)
    out = dict(doc)
    out["screens"] = new_screens
    return out  # type: ignore[return-value]


def _sync_default_regions_into_version(
    entry: AreaEntryDict,
    version_id: str,
) -> tuple[int, int]:
    """Copy base regions into ``versions[V].regions[]`` (without suffix).

    Skips overlay auxiliaries (``_search`` / ``_tap``) and regions already
    present in the version block.

    Returns ``(added, skipped)``.
    """
    import copy as _copy

    ver_block = get_version_block(cast("dict[str, Any]", entry), version_id)
    if ver_block is None:
        return 0, 0

    base_regions = entry.get("regions") or []
    ver_regions = ver_block.get("regions")
    if not isinstance(ver_regions, list):
        ver_regions = []
        ver_block["regions"] = ver_regions
    existing = {
        str(r.get("name", "") or "").strip()
        for r in ver_regions
        if isinstance(r, dict)
    }

    added = 0
    skipped = 0
    for r in base_regions:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "") or "").strip()
        if not name:
            continue
        if is_auxiliary_overlay_region(cast("dict[str, Any]", r)):
            skipped += 1
            continue
        if name in existing:
            skipped += 1
            continue
        ver_regions.append(_copy.deepcopy(r))
        existing.add(name)
        added += 1

    return added, skipped


def _next_entry_id(entries: list[AreaEntryDict]) -> int:
    if not entries:
        return 1
    return max(int(e.get("id", 0)) for e in entries) + 1


def ensure_entry_for_reference_path(
    entries: list[AreaEntryDict],
    ocr_repo_rel: str,
    *,
    references_prefix: str = "references",
) -> int:
    """Find or create an entry whose ``ocr`` points at the selected reference PNG."""
    ocr_norm = ocr_repo_rel.replace("\\", "/").strip()
    target = (REPO_ROOT / ocr_norm).resolve()
    for i, e in enumerate(entries):
        raw = str(e.get("ocr") or "").strip()
        if not raw:
            continue
        try:
            if _resolve_ocr_path_in_reference_context(raw, references_prefix) == target:
                return i
        except OSError:
            continue
    new_e: AreaEntryDict = {
        "id": _next_entry_id(entries),
        "screen_id": "",
        "ocr": _module_local_ocr_for_reference_path(ocr_norm, references_prefix),
        "regions": [],
    }
    entries.append(new_e)
    return len(entries) - 1


def detect_screen_id_from_png_path(path: Path) -> str | None:
    """Run :func:`navigation.detector.suggest_node_for_image_sync` on a PNG file."""
    try:
        from navigation.detector import suggest_node_for_image_sync

        pil = Image.open(path).convert("RGBA")
        bgr = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGBA2BGR)
        return suggest_node_for_image_sync(bgr)
    except Exception:
        return None
