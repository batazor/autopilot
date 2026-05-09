"""Multi-version screen support for ``area.json``.

A screen-entry may declare alternate visual ``versions`` (e.g. ``v2`` for a
high-level hero card whose buttons shifted). Each version has an ``id`` and a
``cond`` — a Python expression evaluated against the player's flat state dict.
Regions belonging to non-default versions are stored in the same ``regions[]``
list with an ``_<version_id>`` suffix (e.g. ``promote_btn_v2``). Resolution is
partial-override: under active ``v2`` the resolver tries ``promote_btn_v2``
first, falls back to ``promote_btn`` if absent.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

VERSION_ID_RE = re.compile(r"^v\d+$")
_VERSION_ID_LOOSE_RE = re.compile(r"^[Vv]?(\d+)$")


def normalize_version_id(raw: str) -> str | None:
    """Best-effort normalize ``"V2"`` / ``" 2 "`` / ``"v02"`` to canonical ``"v2"``.

    Returns ``None`` if input cannot be coerced. Strips leading zeros so
    ``"v02"`` → ``"v2"`` (avoids two ids that look identical in the UI).
    """
    s = (raw or "").strip()
    if not s:
        return None
    m = _VERSION_ID_LOOSE_RE.match(s)
    if not m:
        return None
    n = int(m.group(1))
    return f"v{n}"


def next_version_id(declared_ids: list[str]) -> str:
    """Smallest ``vN`` (N >= 2) not in ``declared_ids``.

    Default version is implicit (treated as ``v1``), so suggestions start at ``v2``.
    """
    used: set[int] = set()
    for raw in declared_ids:
        norm = normalize_version_id(raw)
        if norm:
            used.add(int(norm[1:]))
    n = 2
    while n in used:
        n += 1
    return f"v{n}"
_DOTTED_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_PYTHON_KEYWORDS = frozenset({"True", "False", "None", "and", "or", "not", "in", "is"})


def _rewrite_dotted_idents(expr: str) -> str:
    """Rewrite dotted identifiers (``a.b.c``) to ``_state["a.b.c"]`` lookups.

    Bare identifiers (``level``) and Python keywords are left alone — bare names
    fall through to the eval namespace, where the flat state dict provides them.
    Only multi-segment dotted forms are rewritten, since flat-dict keys use
    dot-notation (``heroes.norah.level``) and Python's ``.`` would mean attribute
    access.
    """

    def repl(match: re.Match[str]) -> str:
        ident = match.group(0)
        head = ident.split(".", 1)[0]
        if head in _PYTHON_KEYWORDS:
            return ident
        return f'_state[{ident!r}]'

    return _DOTTED_IDENT_RE.sub(repl, expr)


def eval_cond(expr: str, state_flat: dict[str, Any]) -> bool:
    """Evaluate a version ``cond`` expression against a flat state dict.

    Returns ``False`` (not raises) for missing keys or evaluation errors so a
    broken/stale cond cannot crash the worker — it simply opts out of activating
    that version.
    """
    expr_str = (expr or "").strip()
    if not expr_str:
        return False
    rewritten = _rewrite_dotted_idents(expr_str)
    try:
        result = eval(rewritten, {"__builtins__": {}}, {"_state": state_flat, **state_flat})
    except KeyError:
        return False
    except Exception as exc:
        logger.warning("eval_cond failed for %r: %s", expr_str, exc)
        return False
    return bool(result)


def compile_cond(expr: str) -> None:
    """Validate cond syntax without state. Raises ``SyntaxError`` if malformed."""
    expr_str = (expr or "").strip()
    if not expr_str:
        raise SyntaxError("cond expression is empty")
    rewritten = _rewrite_dotted_idents(expr_str)
    compile(rewritten, "<cond>", "eval")


def pick_active_version(
    screen_entry: dict[str, Any],
    state_flat: dict[str, Any] | None,
) -> str | None:
    """Return the id of the first version whose ``cond`` is truthy, or ``None``.

    ``None`` means use the default (unsuffixed) regions. Passing ``state_flat=None``
    short-circuits to ``None`` so callers without state context get default behavior.
    """
    if state_flat is None:
        return None
    versions = screen_entry.get("versions") or []
    if not isinstance(versions, list):
        return None
    for ver in versions:
        if not isinstance(ver, dict):
            continue
        vid = str(ver.get("id", "") or "").strip()
        cond = str(ver.get("cond", "") or "").strip()
        if not vid or not cond:
            continue
        if eval_cond(cond, state_flat):
            return vid
    return None


def resolve_region_with_version(
    screen_entry: dict[str, Any],
    region_name: str,
    active_version: str | None,
) -> dict[str, Any] | None:
    """Find the region dict for ``region_name`` in ``screen_entry``.

    With ``active_version`` set, tries ``f"{region_name}_{active_version}"``
    first and falls back to the default ``region_name`` if that override is
    absent (partial-override semantics).
    """
    key = str(region_name or "").strip()
    if not key:
        return None
    regions = screen_entry.get("regions") or []
    if not isinstance(regions, list):
        return None

    candidates: list[str] = []
    if active_version:
        candidates.append(f"{key}_{active_version}")
    candidates.append(key)

    by_name: dict[str, dict[str, Any]] = {}
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        name = str(reg.get("name", "") or "").strip()
        if name:
            by_name[name] = reg
    for cand in candidates:
        if cand in by_name:
            return by_name[cand]
    return None


def split_versioned_name(name: str, known_version_ids: set[str]) -> tuple[str, str | None]:
    """Split a region name into ``(base_name, version_id_or_None)``.

    Only splits if the trailing ``_vN`` suffix matches a version declared in
    ``known_version_ids`` for the entry — this avoids false positives on names
    that happen to end with ``_v3`` for unrelated reasons.
    """
    raw = str(name or "").strip()
    for vid in known_version_ids:
        suffix = f"_{vid}"
        if raw.endswith(suffix) and len(raw) > len(suffix):
            return raw[: -len(suffix)], vid
    return raw, None
