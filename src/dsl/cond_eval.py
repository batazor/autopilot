"""Sandboxed ``cond`` expression evaluator over the flat state dict.

Shared by DSL scenario ``cond:`` guards, overlay-rule ``cond``, navigation
edge conds, and broadcast rules. Expressions are plain Python restricted to a
small AST grammar (literals, comparisons, boolean/arith ops, dotted-state
lookups) — no attribute access, no calls.
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
            msg = (
                f"cond: disallowed node {type(node).__name__} "
                "(only literals, comparisons, boolean/arith ops and dotted-state lookups allowed)"
            )
            raise SyntaxError(
                msg
            )


def _cond_referenced_keys(tree: ast.AST) -> frozenset[str]:
    """State keys the expression can read: bare names + ``_state["a.b.c"]`` slices."""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id != "_state":
            keys.add(node.id)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "_state"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return frozenset(keys)


@lru_cache(maxsize=512)
def _compile_cond_cached(rewritten: str) -> tuple[Any, frozenset[str]]:
    """Parse + validate + compile a cond expression; cache code + referenced keys.

    The referenced-key set lets ``eval_cond`` coerce only the handful of state
    fields the expression actually reads — a player flat-state dict can hold
    thousands of keys, and coercing all of them per rule per overlay tick was
    the dominant cost of cond evaluation.
    """
    tree = ast.parse(rewritten, mode="eval")
    _validate_cond_ast(tree)
    return compile(tree, "<cond>", "eval"), _cond_referenced_keys(tree)


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
    """Coerce Redis/string state values for numeric comparisons."""

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
    """Evaluate a ``cond`` expression against a flat state dict.

    Returns ``False`` (not raises) for missing keys or evaluation errors so a
    broken/stale cond cannot crash the worker — it simply evaluates falsy.
    """
    expr_str = (expr or "").strip()
    if not expr_str:
        return False
    rewritten = _rewrite_dotted_idents(expr_str)
    try:
        code, referenced = _compile_cond_cached(rewritten)
    except SyntaxError as exc:
        logger.warning("eval_cond rejected for %r: %s", expr_str, exc)
        return False
    coerced_state = {
        k: _coerce_cond_value(state_flat[k]) for k in referenced if k in state_flat
    }
    try:
        result = eval(
            code,
            {"__builtins__": {}},
            {"_state": coerced_state, **coerced_state},
        )
    except (KeyError, NameError):
        # State field absent → evaluate falsy. A dotted ident resolves to a dict
        # subscript (KeyError when missing); a bare ident resolves from the
        # injected locals (NameError when missing) — e.g. a flag like
        # ``joe_event_active`` that its producer hasn't written yet. Both mean
        # the same thing and are normal, so stay silent: otherwise this warns on
        # every overlay tick (observed spamming the worker log).
        return False
    except Exception as exc:
        logger.warning("eval_cond failed for %r: %s", expr_str, exc)
        return False
    return bool(result)


def compile_cond(expr: str) -> None:
    """Validate cond syntax without state. Raises ``SyntaxError`` if malformed."""
    expr_str = (expr or "").strip()
    if not expr_str:
        msg = "cond expression is empty"
        raise SyntaxError(msg)
    rewritten = _rewrite_dotted_idents(expr_str)
    _compile_cond_cached(rewritten)
