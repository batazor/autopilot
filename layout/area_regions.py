"""area.json helpers: unique region names and lookup by name (shared by UI and worker)."""

from __future__ import annotations

import math
from typing import Any

from layout.area_versions import (
    VERSION_ID_RE,
    compile_cond,
    pick_active_version,
    resolve_region_with_version,
    split_versioned_name,
)


def is_auxiliary_overlay_region(reg: dict[str, Any]) -> bool:
    """True for overlay search zones, tap helpers, or explicit ``overlay_auxiliary`` flags."""
    if reg.get("overlay_auxiliary"):
        return True
    nm = str(reg.get("name", "") or "").strip()
    return nm.endswith("_search") or nm.endswith("_tap")


def collect_region_name_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Count non-empty region names across all screen entries."""
    counts: dict[str, int] = {}
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            name = str(reg.get("name", "") or "").strip()
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
    return counts


def duplicate_region_names(doc: dict[str, Any]) -> list[str]:
    """Return sorted region names that appear more than once."""
    counts = collect_region_name_counts(doc)
    return sorted(n for n, c in counts.items() if c > 1)


def _deep_almost_equal(
    a: Any,
    b: Any,
    *,
    rel_tol: float = 1e-9,
    abs_tol: float = 1e-6,
) -> bool:
    """Structural equality with tolerant numeric comparison (JSON-like data)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if type(a) is not type(b):
        # Allow int/float interchangeability for numeric leaves.
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_deep_almost_equal(a[k], b[k], rel_tol=rel_tol, abs_tol=abs_tol) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(
            _deep_almost_equal(x, y, rel_tol=rel_tol, abs_tol=abs_tol)
            for x, y in zip(a, b, strict=True)
        )
    if isinstance(a, float):
        return isinstance(b, float) and math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)
    return a == b


def _region_same_content_as_base(override: dict[str, Any], base: dict[str, Any]) -> bool:
    """True if version override matches base region for every key except ``name``."""
    keys_o = {k for k in override if k != "name"}
    keys_b = {k for k in base if k != "name"}
    if keys_o != keys_b:
        return False
    for k in keys_o:
        if not _deep_almost_equal(override[k], base[k]):
            return False
    return True


def dedupe_redundant_version_regions(doc: dict[str, Any]) -> int:
    """Drop ``region_<vid>`` entries that match the base ``region`` on the same screen.

    When saving from the annotator, an override left identical to the default
    (same bbox/options) is redundant — resolution would always fall back to the
    same geometry anyway. Removes those overrides; returns how many regions were
    dropped (mutates ``doc`` in place).
    """
    removed = 0
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        versions = entry.get("versions") or []
        known_ids: set[str] = set()
        if isinstance(versions, list):
            for ver in versions:
                if isinstance(ver, dict):
                    vid = str(ver.get("id", "") or "").strip()
                    if vid:
                        known_ids.add(vid)
        if not known_ids:
            continue
        regions = entry.get("regions")
        if not isinstance(regions, list):
            continue
        by_name: dict[str, dict[str, Any]] = {}
        for reg in regions:
            if isinstance(reg, dict):
                n = str(reg.get("name", "") or "").strip()
                if n:
                    by_name[n] = reg
        to_drop: set[str] = set()
        for reg in regions:
            if not isinstance(reg, dict):
                continue
            name = str(reg.get("name", "") or "").strip()
            base_name, _vid = split_versioned_name(name, known_ids)
            if not base_name or name == base_name:
                continue
            base_reg = by_name.get(base_name)
            if base_reg is None:
                continue
            if _region_same_content_as_base(reg, base_reg):
                to_drop.add(name)
        if to_drop:
            entry["regions"] = [
                r
                for r in regions
                if not (isinstance(r, dict) and str(r.get("name", "") or "").strip() in to_drop)
            ]
            removed += len(to_drop)
    return removed


def validate_unique_region_names(doc: dict[str, Any]) -> None:
    """Raise ValueError if two or more regions share the same non-empty name."""
    dups = duplicate_region_names(doc)
    if dups:
        joined = ", ".join(repr(n) for n in dups)
        raise ValueError(
            f"Duplicate region name(s): {joined}. Each region name must be unique across area.json."
        )


def region_bbox_for_name(
    doc: dict[str, Any],
    name: str,
    state_flat: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the bbox dict for a region by name.

    When ``state_flat`` is provided, the lookup honors the screen-entry's
    ``versions`` list: the first version whose ``cond`` evaluates truthy wins,
    and a ``_<version_id>``-suffixed override is preferred over the default
    region (partial-override semantics). With ``state_flat=None`` the lookup
    behaves as before — only default (unsuffixed) regions match.
    """
    key = str(name or "").strip()
    if not key:
        return None
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        if state_flat is not None:
            active = pick_active_version(entry, state_flat)
            reg = resolve_region_with_version(entry, key, active)
            if reg is not None:
                bbox = reg.get("bbox")
                return bbox if isinstance(bbox, dict) else None
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            if str(reg.get("name", "") or "").strip() != key:
                continue
            bbox = reg.get("bbox")
            if isinstance(bbox, dict):
                return bbox
            return None
    return None


def validate_versions(doc: dict[str, Any]) -> None:
    """Validate screen-entry ``versions`` metadata across the document.

    Checks per entry:
      - Each ``versions[].id`` matches ``^v\\d+$`` and is unique within the entry.
      - Each ``versions[].cond`` is non-empty and parses as a Python expression.
      - Every region whose name ends with ``_<version_id>`` corresponds to a
        declared version in the same entry (no orphan overrides).

    Raises ``ValueError`` on the first violation with a descriptive message.
    """
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        entry_label = f"screen id={entry.get('id')!r} screen_id={entry.get('screen_id')!r}"
        versions = entry.get("versions") or []
        if not isinstance(versions, list):
            raise ValueError(f"{entry_label}: 'versions' must be a list")
        seen_ids: set[str] = set()
        for ver in versions:
            if not isinstance(ver, dict):
                raise ValueError(f"{entry_label}: version entry must be an object, got {type(ver).__name__}")
            vid = str(ver.get("id", "") or "").strip()
            if not VERSION_ID_RE.match(vid):
                raise ValueError(
                    f"{entry_label}: version id {vid!r} must match pattern '^v\\d+$' (e.g. 'v2')"
                )
            if vid in seen_ids:
                raise ValueError(f"{entry_label}: duplicate version id {vid!r}")
            seen_ids.add(vid)
            cond = str(ver.get("cond", "") or "").strip()
            if not cond:
                raise ValueError(f"{entry_label}: version {vid!r} has empty 'cond'")
            try:
                compile_cond(cond)
            except SyntaxError as exc:
                raise ValueError(f"{entry_label}: version {vid!r} cond syntax error: {exc}") from exc

        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            name = str(reg.get("name", "") or "").strip()
            if not name:
                continue
            for vid in seen_ids:
                suffix = f"_{vid}"
                if name.endswith(suffix) and len(name) > len(suffix):
                    break
            else:
                # Name might still end with _vN for an undeclared id — flag those.
                tail = name.rsplit("_", 1)
                if len(tail) == 2 and VERSION_ID_RE.match(tail[1]) and tail[1] not in seen_ids:
                    raise ValueError(
                        f"{entry_label}: region {name!r} has version suffix {tail[1]!r} "
                        f"but no such version is declared in 'versions'"
                    )
