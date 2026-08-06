"""Removed multi-version screen support — transitional shim.

Screen ``versions[]`` are gone (the bot supports established accounts only, so
the single base entry per screen is canonical). The cond evaluator moved to
:mod:`dsl.cond_eval`. The remaining labeling/dashboard consumers are cleaned up
next; this shim keeps their imports alive until then and will be deleted.
"""
from __future__ import annotations

import re
from typing import Any

from dsl.cond_eval import compile_cond, eval_cond  # noqa: F401  (re-export)

VERSION_ID_RE = re.compile(r"^v\d+$")
_VERSION_ID_LOOSE_RE = re.compile(r"^[Vv]?(\d+)$")


def normalize_version_id(raw: str) -> str | None:
    """Best-effort normalize ``"V2"`` / ``" 2 "`` / ``"v02"`` to canonical ``"v2"``."""
    s = (raw or "").strip()
    if not s:
        return None
    m = _VERSION_ID_LOOSE_RE.match(s)
    if not m:
        return None
    n = int(m.group(1))
    return f"v{n}"


def next_version_id(declared_ids: list[str]) -> str:
    """Smallest ``vN`` (N >= 2) not in ``declared_ids``."""
    used: set[int] = set()
    for raw in declared_ids:
        norm = normalize_version_id(raw)
        if norm:
            used.add(int(norm[1:]))
    n = 2
    while n in used:
        n += 1
    return f"v{n}"


def get_version_block(
    screen_entry: dict[str, Any],
    version_id: str | None,
) -> dict[str, Any] | None:
    """Return the legacy ``versions[]`` element with matching id, or ``None``."""
    if not version_id:
        return None
    versions = screen_entry.get("versions") or []
    if not isinstance(versions, list):
        return None
    for ver in versions:
        if not isinstance(ver, dict):
            continue
        if str(ver.get("id", "") or "").strip() == version_id:
            return ver
    return None


def _index_regions_by_name(regions: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(regions, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        name = str(reg.get("name", "") or "").strip()
        if name:
            out[name] = reg
        aliases = reg.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                alias_s = str(alias or "").strip()
                if alias_s:
                    out[alias_s] = reg
    return out


def resolve_region_by_name(
    screen_entry: dict[str, Any],
    region_name: str,
) -> dict[str, Any] | None:
    """Resolve ``region_name`` (or one of its aliases) in the entry's regions."""
    key = str(region_name or "").strip()
    if not key:
        return None
    return _index_regions_by_name(screen_entry.get("regions")).get(key)


def effective_ocr_for_region(
    screen_entry: dict[str, Any],
    region: dict[str, Any],
) -> str:
    """Reference image for any region of the entry: the entry's ``ocr``."""
    return str(screen_entry.get("ocr") or "").strip()


def iter_all_regions(
    screen_entry: dict[str, Any],
) -> list[tuple[dict[str, Any], str | None]]:
    """Yield ``(region, None)`` for every region of the entry."""
    return [
        (reg, None)
        for reg in screen_entry.get("regions") or []
        if isinstance(reg, dict)
    ]
