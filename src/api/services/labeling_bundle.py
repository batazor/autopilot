"""Portable screen-label bundles — share one annotated screen as ``image + labels``.

A bundle is a ``.alabel.zip`` with two members:

- ``screenshot.png`` — the full reference image (the screen entry's ``ocr``), 720x1280.
- ``label.json`` — manifest (see :data:`BUNDLE_KIND` / :data:`BUNDLE_VERSION`) carrying the
  screen's ``screen_id`` + base ``regions`` so a contributor can hand the file to the repo
  owner, who imports it for review before it lands in ``area.yaml``.

Crops (``references/crop/*.png``) are **not** packed — they are derived and regenerated on
save via :func:`dashboard.area_doc.export_all_region_crops_for_area_doc`.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from api.services import labeling_scope as ls
from api.services.labeling import get_labeling_document, save_labeling_regions
from config.module_registry import CORE_MODULE_KEY, normalize_module_scope
from config.reference_naming import TEMPORAL_SUBDIR, temporal_png_abs_path_in_refs
from layout.crop_paths import safe_crop_filename_part

BUNDLE_KIND = "autopilot.screen-label"
BUNDLE_VERSION = 1
MANIFEST_NAME = "label.json"
IMAGE_NAME = "screenshot.png"
TOOL_VERSION = "1"

# Mandatory emulator resolution (see CLAUDE.md → Emulator Requirements).
EXPECTED_WIDTH = 720
EXPECTED_HEIGHT = 1280


class BundleError(ValueError):
    """Raised when a bundle is malformed or fails validation."""


def export_screen_bundle(
    ref_rel: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> tuple[str, bytes]:
    """Pack one annotated screen into an ``.alabel.zip`` and return ``(filename, bytes)``."""
    env = ls.scope_env(scope)
    doc = get_labeling_document(ref_rel, scope=scope)
    png = (env.repo_root / doc["ref"]).resolve().read_bytes()

    with Image.open(io.BytesIO(png)) as img:
        width, height = img.size

    basename = str(doc.get("basename") or "screen").strip() or "screen"
    manifest: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "kind": BUNDLE_KIND,
        "game": env.ctx.game,
        "scope": normalize_module_scope(scope),
        "basename": basename,
        "screen_id": str(doc.get("screen_id") or ""),
        "image": IMAGE_NAME,
        "image_size": {"width": width, "height": height},
        "regions": doc.get("regions") or [],
        "versions": [],
        "source": {
            "contributor": "",
            "app_version": "",
            "exported_at": datetime.now(tz=UTC).isoformat(),
            "tool_version": TOOL_VERSION,
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr(IMAGE_NAME, png)
    return f"{basename}.alabel.zip", buf.getvalue()


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        msg = "label.json: expected an object"
        raise BundleError(msg)
    if manifest.get("kind") != BUNDLE_KIND:
        msg = f"label.json: unexpected kind {manifest.get('kind')!r} (need {BUNDLE_KIND!r})"
        raise BundleError(msg)
    if manifest.get("bundle_version") != BUNDLE_VERSION:
        msg = (
            f"label.json: unsupported bundle_version {manifest.get('bundle_version')!r} "
            f"(need {BUNDLE_VERSION})"
        )
        raise BundleError(msg)
    regions = manifest.get("regions")
    if not isinstance(regions, list):
        msg = "label.json: regions must be a list"
        raise BundleError(msg)
    names = [str(r.get("name") or "") for r in regions if isinstance(r, dict)]
    if len(names) != len(set(names)):
        msg = "label.json: duplicate region names"
        raise BundleError(msg)
    return manifest


def import_screen_bundle(
    content: bytes,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Unpack a bundle into ``temporal/`` for review (does NOT write ``area.yaml``).

    The PNG is staged under the target scope's ``references/temporal/`` and the manifest's
    ``regions`` + ``screen_id`` are returned so the UI can seed the editor; the operator
    reviews and clicks Save (which goes through ``save_labeling_regions``).
    """
    if not content:
        msg = "empty bundle"
        raise BundleError(msg)
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        msg = "not a valid .zip bundle"
        raise BundleError(msg) from exc

    with zf:
        names = set(zf.namelist())
        if MANIFEST_NAME not in names:
            msg = f"bundle missing {MANIFEST_NAME}"
            raise BundleError(msg)
        if IMAGE_NAME not in names:
            msg = f"bundle missing {IMAGE_NAME}"
            raise BundleError(msg)
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = f"{MANIFEST_NAME}: invalid JSON"
            raise BundleError(msg) from exc
        png = zf.read(IMAGE_NAME)

    manifest = _validate_manifest(manifest)

    try:
        with Image.open(io.BytesIO(png)) as img:
            width, height = img.size
    except Exception as exc:
        msg = f"{IMAGE_NAME}: not a readable image"
        raise BundleError(msg) from exc
    if (width, height) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        msg = (
            f"{IMAGE_NAME}: image is {width}x{height}, "
            f"expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}"
        )
        raise BundleError(msg)

    env = ls.scope_env(scope)
    basename = safe_crop_filename_part(str(manifest.get("basename") or "screen"), "screen")
    target = temporal_png_abs_path_in_refs(env.ref_root, basename)
    target.write_bytes(png)
    ref_rel = target.resolve().relative_to(env.repo_root).as_posix()

    warnings: list[str] = []
    if manifest.get("versions"):
        warnings.append("bundle versions are not imported (base regions only)")

    incoming_regions = manifest.get("regions") or []
    screen_id = str(manifest.get("screen_id") or "")
    conflict = _detect_conflict(env, screen_id=screen_id, basename=basename, incoming=incoming_regions)

    return {
        "ok": True,
        "ref": ref_rel,
        "scope": normalize_module_scope(scope),
        "screen_id": screen_id,
        "regions": incoming_regions,
        "image_size": {"width": width, "height": height},
        "source": manifest.get("source") or {},
        "bundle_scope": str(manifest.get("scope") or ""),
        "bundle_game": str(manifest.get("game") or ""),
        "warnings": warnings,
        "conflict": conflict,
    }


def _entry_repo_ref(entry: dict[str, Any], env: ls.LabelingScopeEnv) -> str:
    """Resolve an area entry's ``ocr`` to a repo-relative reference path."""
    ocr = str(entry.get("ocr") or "").replace("\\", "/").strip().lstrip("/")
    if not ocr:
        return ""
    prefix = env.references_prefix
    if ocr == prefix or ocr.startswith(("games/", prefix + "/")):
        return ocr
    return f"{prefix}/{ocr.split('/')[-1]}"


def _region_signature(region: dict[str, Any]) -> tuple:
    """Comparable view of a region's meaningful fields (name-independent equality)."""
    bbox = region.get("bbox") or {}
    rounded = tuple(
        round(float(bbox.get(k, 0) or 0), 3) for k in ("x", "y", "width", "height")
    )
    return (
        str(region.get("action") or ""),
        rounded,
        region.get("threshold"),
        bool(region.get("has_red_dot")),
        str(region.get("type") or ""),
    )


def compute_region_diff(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Classify regions by ``name``: added / removed / changed / unchanged."""
    ex = {str(r.get("name") or ""): r for r in existing if isinstance(r, dict)}
    inc = {str(r.get("name") or ""): r for r in incoming if isinstance(r, dict)}
    ex.pop("", None)
    inc.pop("", None)
    added = sorted(inc.keys() - ex.keys())
    removed = sorted(ex.keys() - inc.keys())
    changed, unchanged = [], []
    for name in sorted(ex.keys() & inc.keys()):
        if _region_signature(ex[name]) == _region_signature(inc[name]):
            unchanged.append(name)
        else:
            changed.append(name)
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def _detect_conflict(
    env: ls.LabelingScopeEnv,
    *,
    screen_id: str,
    basename: str,
    incoming: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find an existing screen in ``scope`` matching by ``screen_id`` OR reference basename."""
    doc = ls.load_area_doc(env)
    screens = doc.get("screens")
    if not isinstance(screens, list):
        return None
    sid = screen_id.strip()
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        entry_sid = str(entry.get("screen_id") or "").strip()
        entry_ref = _entry_repo_ref(entry, env)
        entry_base = Path(entry_ref).stem if entry_ref else ""
        if (sid and entry_sid == sid) or (entry_base and entry_base == basename):
            existing_regions = [r for r in (entry.get("regions") or []) if isinstance(r, dict)]
            return {
                "existing_ref": entry_ref,
                "existing_screen_id": entry_sid,
                "existing_regions": existing_regions,
                "existing_has_image": bool(entry_ref)
                and (env.repo_root / entry_ref).is_file(),
                "matched_by": "screen_id" if (sid and entry_sid == sid) else "basename",
                "diff": compute_region_diff(existing_regions, incoming),
            }
    return None


def apply_imported_bundle(
    *,
    scope: str,
    staged_ref: str,
    target_ref: str,
    regions: list[dict[str, Any]],
    screen_id: str | None = None,
    use_incoming_image: bool = False,
) -> dict[str, Any]:
    """Resolve an import conflict: publish the chosen PNG and write merged regions.

    ``staged_ref`` is the temporal PNG produced by :func:`import_screen_bundle`; ``target_ref``
    is the existing published reference to update. When ``use_incoming_image`` is set the staged
    bytes overwrite the published PNG, otherwise the existing PNG is kept. The temporal staging
    file is removed either way. Regions are written through :func:`save_labeling_regions` so
    crops + rename cascades + SSE all happen as on a normal save.
    """
    env = ls.scope_env(scope)
    staged = (env.repo_root / staged_ref.replace("\\", "/").strip().lstrip("/")).resolve()
    target = (env.repo_root / target_ref.replace("\\", "/").strip().lstrip("/")).resolve()
    if ".." in Path(staged_ref).parts or ".." in Path(target_ref).parts:
        msg = "invalid path"
        raise BundleError(msg)

    if use_incoming_image:
        if not staged.is_file():
            msg = f"staged image not found: {staged_ref}"
            raise BundleError(msg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(staged.read_bytes())
    elif not target.is_file():
        msg = f"existing reference not found: {target_ref}"
        raise BundleError(msg)

    # Drop the temporal staging file (it lived under references/temporal/).
    if staged.is_file() and TEMPORAL_SUBDIR in staged.parts:
        staged.unlink()

    result = save_labeling_regions(
        target_ref,
        regions,
        screen_id=screen_id,
        scope=scope,
    )
    return {**result, "ref": target_ref, "scope": normalize_module_scope(scope)}
