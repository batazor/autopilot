"""Multi-version screen support for ``area.json``.

A screen entry may declare alternate visual ``versions`` (e.g. ``v2`` for a
high-level hero card whose buttons shifted). Each version has an ``id``, a
``cond`` (Python expression evaluated against the player's flat state dict),
its own optional ``ocr`` reference image, its own ``regions[]`` list of
overrides, and an optional ``removed[]`` list of base region names that
should be treated as absent in this version.

Resolution rules (see :func:`resolve_region_with_version`):

1. If ``active_version`` is set and ``region_name`` is in
   ``versions[active].removed`` → the region is treated as absent.
2. Else if ``versions[active].regions[]`` contains a region with that name →
   return it (override or version-only addition).
3. Else fall back to the entry's base ``regions[]``.
"""
from __future__ import annotations

import ast
import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# Node types allowed inside a ``cond`` expression. ``__builtins__: {}`` alone
# does NOT sandbox eval — a payload like ``(0).__class__.__mro__[1].__subclasses__()``
# escapes via attribute traversal on a literal int. Restricting the AST to a
# small grammar (literals, comparisons, boolean/arith ops, dotted-state lookup)
# blocks attribute access and calls entirely. ``cond`` strings come from repo
# YAML today, but this still hardens the sandbox if user-provided conds are
# ever accepted (debug UI, custom modules, etc.).
_COND_ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset({
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BoolOp, ast.And, ast.Or,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.IfExp,
    # Subscript needed for the ``_state["dotted.key"]`` lookup synthesised by
    # ``_rewrite_dotted_idents``. ``Tuple`` enables ``x in (a, b)``.
    ast.Subscript, ast.Tuple, ast.List,
})


def _validate_cond_ast(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if type(node) not in _COND_ALLOWED_NODES:
            raise SyntaxError(
                f"cond: disallowed node {type(node).__name__} "
                "(only literals, comparisons, boolean/arith ops and dotted-state lookups allowed)"
            )


@lru_cache(maxsize=512)
def _compile_cond_cached(rewritten: str) -> Any:
    """Parse + validate + compile a cond expression; cache the code object."""
    tree = ast.parse(rewritten, mode="eval")
    _validate_cond_ast(tree)
    return compile(tree, "<cond>", "eval")

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
_INT_STRING_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_STRING_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)$")


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


def _coerce_cond_value(value: Any) -> Any:
    """Coerce Redis/string state values for numeric version comparisons."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if _INT_STRING_RE.match(text):
        try:
            return int(text)
        except ValueError:
            return value
    if _FLOAT_STRING_RE.match(text):
        try:
            return float(text)
        except ValueError:
            return value
    return value


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
        code = _compile_cond_cached(rewritten)
    except SyntaxError as exc:
        logger.warning("eval_cond rejected for %r: %s", expr_str, exc)
        return False
    coerced_state = {k: _coerce_cond_value(v) for k, v in state_flat.items()}
    try:
        result = eval(
            code,
            {"__builtins__": {}},
            {"_state": coerced_state, **coerced_state},
        )
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
    _compile_cond_cached(rewritten)


def pick_active_version(
    screen_entry: dict[str, Any],
    state_flat: dict[str, Any] | None,
) -> str | None:
    """Return the id of the first version whose ``cond`` is truthy, or ``None``.

    ``None`` means use the default (base) regions. Passing ``state_flat=None``
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


def get_version_block(
    screen_entry: dict[str, Any],
    version_id: str | None,
) -> dict[str, Any] | None:
    """Return the ``versions[]`` element with matching id, or ``None``."""
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


def resolve_region_with_version(
    screen_entry: dict[str, Any],
    region_name: str,
    active_version: str | None,
) -> dict[str, Any] | None:
    """Resolve ``region_name`` honoring the active version.

    Order:
      1. If the version declares the name in ``removed[]`` — return ``None``
         (region is intentionally absent in this version).
      2. If the version's ``regions[]`` has the name — return it.
      3. Else fall back to the entry's base ``regions[]``.

    Returns ``None`` if neither place has the region (or it was removed).
    """
    key = str(region_name or "").strip()
    if not key:
        return None

    ver_block = get_version_block(screen_entry, active_version)
    if ver_block is not None:
        removed = ver_block.get("removed") or []
        if isinstance(removed, list) and key in {
            str(x).strip() for x in removed if isinstance(x, str)
        }:
            return None
        ver_regions = _index_regions_by_name(ver_block.get("regions"))
        if key in ver_regions:
            return ver_regions[key]

    base = _index_regions_by_name(screen_entry.get("regions"))
    return base.get(key)


def region_version_of(
    screen_entry: dict[str, Any],
    region: dict[str, Any],
) -> str | None:
    """Return the version id whose ``regions[]`` contains this region, or ``None`` for base.

    Identity-based lookup — pass the dict you got from
    :func:`resolve_region_with_version` (or by walking the structure directly).
    """
    versions = screen_entry.get("versions") or []
    if not isinstance(versions, list):
        return None
    for ver in versions:
        if not isinstance(ver, dict):
            continue
        ver_regions = ver.get("regions") or []
        if not isinstance(ver_regions, list):
            continue
        for r in ver_regions:
            if r is region:
                vid = str(ver.get("id", "") or "").strip()
                return vid or None
    return None


def effective_ocr_for_region(
    screen_entry: dict[str, Any],
    region: dict[str, Any],
) -> str:
    """Reference image to use for ``region``.

    Regions inside a ``versions[]`` block use that version's ``ocr`` if set,
    falling back to the entry's default ``ocr``. Base regions always use the
    entry's default ``ocr``.
    """
    default_ocr = str(screen_entry.get("ocr") or "").strip()
    vid = region_version_of(screen_entry, region)
    if not vid:
        return default_ocr
    ver_block = get_version_block(screen_entry, vid)
    if ver_block is None:
        return default_ocr
    ver_ocr = str(ver_block.get("ocr") or "").strip()
    return ver_ocr or default_ocr


def iter_all_regions(
    screen_entry: dict[str, Any],
) -> list[tuple[dict[str, Any], str | None]]:
    """Yield ``(region, version_id_or_None)`` for every region across base + all versions.

    Used by validation, autocomplete, and crop export — anything that needs to
    walk every region regardless of which version is active.
    """
    out: list[tuple[dict[str, Any], str | None]] = []
    for reg in screen_entry.get("regions") or []:
        if isinstance(reg, dict):
            out.append((reg, None))
    for ver in screen_entry.get("versions") or []:
        if not isinstance(ver, dict):
            continue
        vid = str(ver.get("id", "") or "").strip() or None
        for reg in ver.get("regions") or []:
            if isinstance(reg, dict):
                out.append((reg, vid))
    return out
