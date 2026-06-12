"""Stateless helpers for the DSL scenario executor.

Pulled out of ``tasks/dsl_scenario.py`` so the main file stays focused on the
``DslScenarioTask`` runtime. Everything here is pure functions over plain
inputs (dicts, strings, paths, redis client) — no class state.

External callers should still import ``DslScenarioTask`` from
``tasks.dsl_scenario``; this module is internal.
"""
from __future__ import annotations

import logging
import random
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, SupportsFloat, cast

import yaml
from rapidfuzz import fuzz

from adb import _redis
from config.event_timers import event_timer_remaining_seconds, read_event_timer
from config.paths import repo_root as _repo_root  # noqa: F401

# Re-exported so ``tasks.dsl_scenario`` can pull it through to the test surface,
# where tests monkeypatch ``dsl_scenario._repo_root`` to a tmp_path. The actual
# implementation lives in ``config.paths.repo_root``.

logger = logging.getLogger(__name__)


class _BreakRepeat(Exception):
    """Internal control-flow: break the nearest loop-like block."""


def _trace_exec_result_kwargs(row: dict[str, Any]) -> dict[str, Any]:
    """Return exec-handler result fields safe to pass to ``_append_trace_row``.

    ``status`` is already the positional trace-row outcome (``ok``,
    ``stopped``, ...). Exec handlers may also return a domain-specific status
    such as Dreamscape's ``won`` / ``lost``; keep that value under
    ``exec_status`` instead of colliding with the trace method signature.
    """
    if "status" not in row:
        return row
    safe = dict(row)
    safe["exec_status"] = safe.pop("status")
    return safe


def _step_bool_guard(step: dict[str, Any], key: str) -> bool | None:
    """Read optional ``key: true|false`` on a DSL step (YAML bool only).

    Returns ``None`` when the field is absent — caller keeps default behaviour.
    """
    if not isinstance(step, dict) or key not in step:
        return None
    raw = step.get(key)
    return raw if isinstance(raw, bool) else None


def _step_red_dot_requirement(step: dict[str, Any]) -> bool | None:
    """Read optional ``isRedDot: true|false`` on a ``match:`` / ``while_match:`` step."""
    return _step_bool_guard(step, "isRedDot")


def _step_white_border_requirement(step: dict[str, Any]) -> bool | None:
    """Read optional ``isWhiteBorder: true|false`` on a ``match:`` / ``while_match:`` step."""
    return _step_bool_guard(step, "isWhiteBorder")


def _step_tab_active_requirement(step: dict[str, Any]) -> bool | None:
    """Read optional ``isTabActive: true|false`` on a ``match:`` / ``while_match:`` step."""
    return _step_bool_guard(step, "isTabActive")


def _step_yellow_glow_requirement(step: dict[str, Any]) -> bool | None:
    """Read optional ``isYellowGlow: true|false`` on a ``match:`` / ``while_match:`` step.

    Mirrors the ``isRedDot`` / ``isTabActive`` flags — the step asks "is
    there a claimable yellow-rim tile inside <region>?". Used by reward
    grids like ``shop.to.dawn_fund.box``.
    """
    return _step_bool_guard(step, "isYellowGlow")


# ---------------------------------------------------------------------------
# Color checks (dominant color in a bbox)
# ---------------------------------------------------------------------------

_COLOR_WORD_ALIASES: dict[str, str] = {
    "red": "red",
    "blue": "blue",
    "gray": "gray",
    "grey": "gray",
    "green": "green",
}

# Simple guard for DSL steps, e.g. ``cond: currentNode != main_city`` (skip when false).
# LHS is anchored to the known screen tokens — otherwise the regex would
# greedily capture any ``word op word`` cond (e.g. ``active_player != null``)
# and route it through screen-cond before text-cond gets a look-in.
_COND_SCREEN_RE = re.compile(
    r"^\s*(?P<lhs>currentNode|current_node|current_screen)\s*(?P<op>==|!=)\s*(?P<rhs>[\w.-]+)\s*$",
    re.IGNORECASE,
)
_COND_SCREEN_LHS = frozenset({"currentnode", "current_node", "current_screen"})
# RHS tokens that mean Redis ``current_screen`` is unset / overlay ``screens: [none]``.
_COND_SCREEN_UNKNOWN_RHS = frozenset({"none", "unknown", "empty"})

# Instance-state text guards, e.g. ``cond: chapter.task ~= "Upgrade 2"``.
# - lhs is a Redis hash field in `wos:instance:<id>:state`
# - op:
#   - `~=`: case-insensitive substring contains; RHS may use ``|`` for alternatives
#     (e.g. ``"Upgrade|Build"`` matches if any alternative is a substring)
#   - `!~`: negated case-insensitive substring contains; RHS also supports ``|``
#   - `~~`: case-insensitive FUZZY contains (rapidfuzz partial_ratio) — like `~=`
#     but tolerant of OCR character noise (``"Vlctory"`` still matches ``"victory"``).
#     Optional inline threshold ``~~90`` (0–100, default ``_COND_FUZZ_THRESHOLD``);
#     RHS may use ``|`` alternatives, matching if any clears the threshold.
#   - `==` / `!=`: case-insensitive full-string match
# The ``~~`` branch carries its own optional threshold group so the digits are
# only consumed after ``~~`` (``count == 90`` keeps ``90`` as the RHS).
_COND_TEXT_RE = re.compile(
    r'^\s*(?P<lhs>[\w.\-:]+)\s*'
    r'(?P<op>==|!=|!~|~=|~~(?P<thr>\d{1,3})?)\s*'
    r'(?P<rhs>"[^"]*"|\'[^\']*\'|.+?)\s*$'
)
# Default similarity cutoff (0–100) for the ``~~`` fuzzy operator when no inline
# threshold is given. Matches the spirit of the solver's fuzzy tap recovery.
_COND_FUZZ_THRESHOLD = 85.0
# Bare RHS tokens that mean "field is unset / empty". Lets scenarios write
# ``cond: active_player != null`` to gate on ``who_i_am`` having completed —
# the canonical idiom for "this scenario needs a player binding". Without
# this normalisation the rhs would be the literal string ``"null"`` and the
# comparison would always succeed when the field holds a real player id.
_COND_TEXT_EMPTY_TOKENS = frozenset({"null", "nil", "none", "empty"})

# ``loop`` / ``repeat`` / ``while_match`` also nest ``steps``; composite blocks use only ``cond`` + ``steps``.
_DSL_STEP_ACTION_KEYS = frozenset({
    "match",
    "while_match",
    "while_scroll",
    "repeat",
    "loop",
    "push_scenario",
    "swipe_direction",
    "tap",
    "swipe",
    "type_text",
    "ocr",
    "exec",
    "click",
    "wait_screen",
    "wait",
    "system_back",
})


def _dsl_step_summary(step: Any) -> str:
    """Short human-readable label for queue/history step traces."""
    if not isinstance(step, dict):
        return "(invalid)"
    base: str | None = None
    for key in (
        "click",
        "match",
        "while_match",
        "while_scroll",
        "ocr",
        "type_text",
        "swipe_direction",
        "push_scenario",
        "exec",
        "wait_screen",
        "wait",
        "repeat",
        "loop",
        "system_back",
    ):
        if key not in step:
            continue
        val = step[key]
        if key in ("click", "match", "while_match", "while_scroll", "ocr"):
            s = str(val).strip()
            base = f"{key}:{s[:48]}{'…' if len(s) > 48 else ''}"
        elif key == "repeat":
            base = "repeat"
        elif key == "loop":
            base = "loop"
        elif key == "swipe_direction":
            base = f"swipe:{str(val)[:40]}"
        elif key == "push_scenario":
            base = f"push:{str(val)[:40]}"
        elif key == "exec":
            base = f"exec:{str(val)[:40]}"
        elif key == "type_text":
            base = f"type_text:{len(str(val))} chars"
        elif key == "wait_screen":
            base = f"wait_screen:{str(val)[:40]}"
        elif key == "wait":
            base = f"wait:{str(val)[:24]}"
        break
    if base is None:
        if "steps" in step and isinstance(step.get("steps"), list):
            base = f"group({len(step['steps'])})"
        else:
            extra = [k for k in step if k != "cond"]
            base = ",".join(extra[:5]) or "(empty)"
    # Append truthy guard flags so two otherwise-identical steps with different
    # guards (e.g. ``while_match: button.claim`` with and without
    # ``isWhiteBorder``) render distinctly in the trace.
    guards = [g for g in ("isRedDot", "isWhiteBorder", "isTabActive") if step.get(g)]
    if guards:
        base = f"{base} [{','.join(guards)}]"
    return base


def _eval_simple_screen_cond(expr: str, current_screen: str) -> bool:
    """Evaluate ``lhs == rhs`` / ``lhs != rhs`` where *lhs* is Redis ``current_screen``."""
    m = _COND_SCREEN_RE.match(expr.strip())
    if not m:
        logger.warning("dsl_scenario: unsupported cond syntax %r — skipping step", expr)
        return False
    lhs_raw = m.group("lhs").strip().lower().replace("-", "_")
    if lhs_raw not in _COND_SCREEN_LHS:
        logger.warning("dsl_scenario: unknown cond lhs %r — skipping step", m.group("lhs"))
        return False
    op = m.group("op")
    rhs = m.group("rhs").strip()
    cur = current_screen.strip()
    cur_lc = cur.lower()
    rhs_lc = rhs.lower()
    if op == "==":
        if rhs_lc in _COND_SCREEN_UNKNOWN_RHS:
            return cur_lc == "" or cur_lc in _COND_SCREEN_UNKNOWN_RHS
        return cur_lc == rhs_lc
    if rhs_lc in _COND_SCREEN_UNKNOWN_RHS:
        return cur_lc != "" and cur_lc not in _COND_SCREEN_UNKNOWN_RHS
    return cur_lc != rhs_lc


def _decode_redis_value(raw: Any) -> str:
    """Normalise a raw Redis value to a stripped ``str``.

    The async client (``redis.asyncio``) is created without
    ``decode_responses=True`` (see ``worker.instance_worker._connect``), so
    ``hget`` returns ``bytes``. ``str(b"main_city")`` produces the literal
    ``"b'main_city'"`` rather than the value, which silently breaks any
    equality check against the configured node name (e.g. ``cond:
    currentNode != main_city`` would always be true). Always decode bytes
    before returning.
    """

    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw).strip()


async def _read_current_screen(instance_id: str, redis_async: Any | None) -> str:
    key = f"wos:instance:{instance_id}:state"
    field = "current_screen"
    if redis_async is not None:
        try:
            raw = await redis_async.hget(key, field)
            return _decode_redis_value(raw)
        except Exception:
            logger.debug("redis async hget current_screen failed", exc_info=True)
    try:
        return _decode_redis_value(_redis().hget(key, field))
    except Exception:
        logger.debug("redis sync hget current_screen failed", exc_info=True)
        return ""


async def _read_instance_state_field(
    instance_id: str, field: str, redis_async: Any | None
) -> str:
    key = f"wos:instance:{instance_id}:state"
    field = str(field or "").strip()
    if not field:
        return ""
    if redis_async is not None:
        try:
            raw = await redis_async.hget(key, field)
            return _decode_redis_value(raw)
        except Exception:
            logger.debug("redis async hget state field failed", exc_info=True)
    try:
        return _decode_redis_value(_redis().hget(key, field))
    except Exception:
        logger.debug("redis sync hget state field failed", exc_info=True)
        return ""


async def _read_active_player(instance_id: str, redis_async: Any | None) -> str:
    return await _read_instance_state_field(instance_id, "active_player", redis_async)


async def _read_player_state_field(
    player_id: str, field: str, redis_async: Any | None
) -> str:
    pid = str(player_id or "").strip()
    field = str(field or "").strip()
    if not pid or not field:
        return ""
    key = f"wos:player:{pid}:state"
    if redis_async is not None:
        try:
            raw = await redis_async.hget(key, field)
            return _decode_redis_value(raw)
        except Exception:
            logger.debug("redis async hget player state field failed", exc_info=True)
    try:
        return _decode_redis_value(_redis().hget(key, field))
    except Exception:
        logger.debug("redis sync hget player state field failed", exc_info=True)
        return ""


def _strip_quotes(s: str) -> str:
    s2 = (s or "").strip()
    if len(s2) >= 2 and ((s2[0] == '"' and s2[-1] == '"') or (s2[0] == "'" and s2[-1] == "'")):
        return s2[1:-1]
    # Unicode “smart” quotes (copy-paste / some editors).
    if len(s2) >= 2 and (s2[0] in "“‘" and s2[-1] in "”’"):
        return s2[1:-1]
    return s2


async def _eval_instance_text_cond(expr: str, instance_id: str, redis_async: Any | None) -> bool:
    m = _COND_TEXT_RE.match(expr.strip())
    if not m:
        return False
    lhs = str(m.group("lhs") or "").strip()
    op = str(m.group("op") or "").strip()
    rhs = _strip_quotes(str(m.group("rhs") or ""))
    if not lhs:
        return False
    # Prefer player-scoped state — that's where OCR ``store:`` writes by default
    # (``ocr: page.squad_settings.status / store: squad_status`` lands in
    # ``wos:player:<pid>:state``). Fall back to instance state for system fields
    # (``current_screen``-adjacent) and DSL bookkeeping (``dsl_last_match_*``).
    cur = ""
    pid = await _read_active_player(instance_id, redis_async)
    if pid:
        cur = await _read_player_state_field(pid, lhs, redis_async)
    if not cur:
        cur = await _read_instance_state_field(instance_id, lhs, redis_async)
    cur_lc = cur.strip().lower()
    rhs_lc = rhs.strip().lower()
    # Bare ``null`` / ``none`` / ``nil`` / ``empty`` on the right-hand side
    # means "the field has no value" — without this normalisation a scenario
    # writing ``cond: active_player != null`` would compare against the
    # literal string ``"null"`` and almost always pass, defeating the gate.
    # Quoted forms (e.g. ``!= "null"``) keep their literal meaning.
    if rhs_lc in _COND_TEXT_EMPTY_TOKENS and (m.group("rhs") or "").strip()[:1] not in {'"', "'"}:
        rhs_lc = ""
    if op == "~=":
        parts = [p.strip() for p in rhs_lc.split("|")]
        alts = [p for p in parts if p]
        return bool(alts) and any(a in cur_lc for a in alts)
    if op == "!~":
        parts = [p.strip() for p in rhs_lc.split("|")]
        alts = [p for p in parts if p]
        return bool(alts) and not any(a in cur_lc for a in alts)
    if op.startswith("~~"):
        thr_raw = m.group("thr")
        threshold = float(thr_raw) if thr_raw else _COND_FUZZ_THRESHOLD
        parts = [p.strip() for p in rhs_lc.split("|")]
        alts = [p for p in parts if p]
        if not alts or not cur_lc:
            return False
        return any(fuzz.partial_ratio(a, cur_lc) >= threshold for a in alts)
    if op == "==":
        return cur_lc == rhs_lc
    if op == "!=":
        return cur_lc != rhs_lc
    return False


async def _dsl_cond_allows_step(
    step: dict[str, Any],
    instance_id: str,
    redis_async: Any | None,
    state_flat: dict[str, Any] | None = None,
) -> bool:
    raw = step.get("cond")
    if raw is None or isinstance(raw, bool):
        return True
    s = str(raw).strip()
    if not s:
        return True
    if _COND_SCREEN_RE.match(s):
        cur = await _read_current_screen(instance_id, redis_async)
        return _eval_simple_screen_cond(s, cur)
    if _COND_TEXT_RE.match(s):
        return await _eval_instance_text_cond(s, instance_id, redis_async)
    # Fallback: arithmetic / boolean expression evaluated against the player's
    # flat state dict. Lets scenarios gate on resource thresholds and computed
    # comparisons (e.g. ``exploration.state.myPower * 1.2 >= exploration.state.enemyPower``).
    # ``eval_cond`` swallows runtime errors and returns ``False`` so a stale or
    # broken expression cannot crash the worker — the scenario simply skips.
    if state_flat is not None:
        from layout.area_versions import eval_cond as _eval_state_expr

        return _eval_state_expr(s, state_flat)
    logger.warning("dsl_scenario: unsupported cond syntax %r — skipping step", s)
    return False



_HMS_RE = re.compile(
    r"(?:(?P<days>\d{1,4})\s*(?:days?|d)\s*)?"
    r"(?:(?P<hours>\d{1,3}):)?"
    r"(?P<minutes>\d{1,2}):(?P<seconds>\d{2})",
    re.IGNORECASE,
)
_DURATION_SUFFIX_RE = re.compile(r"^\d+(?:\.\d+)?\s*(?:ms|s|m|h)$", re.IGNORECASE)


def _parse_hms_to_seconds(text: str) -> int | None:
    """Parse OCR'd time strings like ``"00:01:23"`` / ``"1:23:45"`` /
    ``"05:30"`` / ``"1d 09:11:19"`` into total seconds.

    Returns ``None`` when no recognizable H:M:S or M:SS group is found.

    Robust to surrounding noise (whitespace, leading labels, units): scans
    for the first colon-separated digit group in the string. The seconds
    field is required to be 2 digits — single-digit "1:2" would otherwise
    collide with arbitrary numbers like a score "5:3" and produce garbage.
    """
    s = (text or "").strip()
    if not s:
        return None
    m = _HMS_RE.search(s)
    if m is None:
        return None
    try:
        days = int(m.group("days") or 0)
        h = int(m.group("hours") or 0)
        mn = int(m.group("minutes"))
        sec = int(m.group("seconds"))
    except (TypeError, ValueError):
        return None
    if mn >= 60 or sec >= 60:
        return None
    return days * 86400 + h * 3600 + mn * 60 + sec


def _event_timer_name_from_spec(spec: object) -> str:
    """Extract an event timer key from an OCR ``event_timer:`` step field."""
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        spec_map = cast("dict[str, Any]", spec)
        for key in ("name", "event", "key"):
            raw = spec_map.get(key)
            if raw is not None:
                value = str(raw).strip()
                if value:
                    return value
    return ""


async def _resolve_event_timer_delay_seconds(
    event_name: str,
    *,
    instance_id: str,
    redis_async: Any | None,
    player_id: str | None = None,
) -> float | None:
    pid = str(player_id or "").strip()
    if not pid:
        pid = await _read_active_player(instance_id, redis_async)
    if not pid:
        return None
    timer = read_event_timer(pid, event_name)
    if timer is None:
        return None
    delay_s = event_timer_remaining_seconds(timer)
    if delay_s is None:
        logger.warning(
            "push_scenario: event timer %r for player=%s is invalid — skipping push",
            event_name,
            pid,
        )
    return delay_s


async def _resolve_push_delay_base_seconds(
    delay: object,
    *,
    instance_id: str,
    redis_async: Any | None,
    player_id: str | None = None,
) -> float | None:
    """Resolve a ``push_scenario.delay`` spec into base seconds (no scale/pad).

    Returns:
      - ``0.0`` — no delay specified (caller enqueues immediately).
      - ``float`` ≥ 0 — delay resolved.
      - ``None`` — ``delay`` WAS specified but could not be resolved (state
        field empty / value not parseable). The caller MUST skip the
        ``push_scenario`` entirely. This is the DSL-level guard against a
        missed OCR re-pushing the same scenario with delay 0 in a tight loop.

    Three forms when ``delay`` is truthy:
      1. Suffix literal — ``"500ms"`` / ``"30s"`` / ``"15m"`` / ``"6h"``.
      2. ``hh:mm:ss`` / ``mm:ss`` literal — any string containing ``:``.
      3. SQLite event timer key — ``event_timers[<delay>]`` for the active
         player, where the OCR step stored a durable reset snapshot.
      4. State field reference — any other non-empty string (e.g.
         ``"artisans_trove.delay"``). Resolved against player-scoped state
         first (where ``ocr: store:`` lands by default), then instance-scoped.
         The fetched value must itself be ``hh:mm:ss``.

    Bare numbers (``60`` / ``"60"``) are NOT accepted — they're ambiguous
    with state-field names and would silently mask a missed OCR. Use ``"60s"``.
    """
    if delay is None:
        return 0.0
    s = str(delay).strip()
    if not s:
        return 0.0

    if ":" in s:
        parsed = _parse_hms_to_seconds(s)
        if parsed is not None:
            return float(parsed)
        logger.warning(
            "push_scenario: failed to parse delay time literal %r — skipping push", s
        )
        return None

    if _DURATION_SUFFIX_RE.match(s):
        return _parse_wait_seconds(s)

    event_delay = await _resolve_event_timer_delay_seconds(
        s,
        instance_id=instance_id,
        redis_async=redis_async,
        player_id=player_id,
    )
    if event_delay is not None:
        return event_delay

    pid = str(player_id or "").strip()
    if not pid:
        pid = await _read_active_player(instance_id, redis_async)
    cur = ""
    if pid:
        cur = await _read_player_state_field(pid, s, redis_async)
    if not cur:
        cur = await _read_instance_state_field(instance_id, s, redis_async)
    if not cur:
        logger.warning(
            "push_scenario: delay state field %r unset — skipping push "
            "(player=%s, instance=%s)",
            s, pid or "-", instance_id,
        )
        return None
    parsed = _parse_hms_to_seconds(cur)
    if parsed is None:
        logger.warning(
            "push_scenario: state value %r=%r is not hh:mm:ss — skipping push",
            s, cur,
        )
        return None
    return float(parsed)


# ── Delay expression support ────────────────────────────────────────────────
# ``push_scenario.delay`` may be a small arithmetic expression mixing an
# OCR/state-field reference, duration literals and bare numeric factors — e.g.
# ``mp_ttl * 2 + 3s`` (march there and back, plus a settle window). Operators
# ``+ - * /`` with the usual precedence; no parentheses. Any operand that fails
# to resolve (a missed OCR field, say) collapses the whole expression to None so
# the caller skips the push instead of re-firing on a degenerate delay.
_DELAY_TOKEN_RE = re.compile(r"[+\-*/]|[^+\-*/\s]+")
_DELAY_OP_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


async def _resolve_delay_operand(
    token: str,
    *,
    instance_id: str,
    redis_async: Any | None,
    player_id: str | None,
) -> float | None:
    """Resolve one delay-expression operand to seconds (or a bare factor).

    A pure number is a dimensionless literal (e.g. the ``2`` in ``ttl * 2``);
    everything else (duration / ``hh:mm:ss`` / event timer / state field) goes
    through :func:`_resolve_push_delay_base_seconds`.
    """
    try:
        return float(token)
    except ValueError:
        pass
    return await _resolve_push_delay_base_seconds(
        token,
        instance_id=instance_id,
        redis_async=redis_async,
        player_id=player_id,
    )


def _eval_delay_rpn(items: list[tuple[str, object]]) -> float:
    """Evaluate tokenized infix (``('val', float)`` / ``('op', str)``) via shunting-yard.

    Tokens must alternate value/operator and start+end on a value (no missing
    operators, no trailing/leading operator); anything else is ``ValueError``.
    """
    malformed = "malformed delay expression"
    if not items or items[-1][0] != "val":
        raise ValueError(malformed)
    for idx, item in enumerate(items):
        if item[0] != ("val" if idx % 2 == 0 else "op"):
            raise ValueError(malformed)

    output: list[object] = []
    ops: list[str] = []
    for kind, value in items:
        if kind == "val":
            output.append(value)
            continue
        op = str(value)
        while ops and _DELAY_OP_PRECEDENCE[ops[-1]] >= _DELAY_OP_PRECEDENCE[op]:
            output.append(ops.pop())
        ops.append(op)
    while ops:
        output.append(ops.pop())

    stack: list[float] = []
    for tok in output:
        if isinstance(tok, str):
            b = stack.pop()
            a = stack.pop()
            if tok == "+":
                stack.append(a + b)
            elif tok == "-":
                stack.append(a - b)
            elif tok == "*":
                stack.append(a * b)
            else:  # "/"
                stack.append(a / b)
        else:
            # Non-op tokens are the ('val', float) payloads by construction.
            stack.append(float(cast("SupportsFloat", tok)))
    if len(stack) != 1:
        raise ValueError(malformed)
    return stack[0]


async def _eval_delay_expression(
    expr: str,
    *,
    instance_id: str,
    redis_async: Any | None,
    player_id: str | None,
) -> float | None:
    items: list[tuple[str, object]] = []
    for token in _DELAY_TOKEN_RE.findall(expr):
        if token in _DELAY_OP_PRECEDENCE:
            items.append(("op", token))
            continue
        operand = await _resolve_delay_operand(
            token,
            instance_id=instance_id,
            redis_async=redis_async,
            player_id=player_id,
        )
        if operand is None:
            logger.warning(
                "push_scenario: delay operand %r unresolved in %r — skipping push",
                token, expr,
            )
            return None
        items.append(("val", operand))
    try:
        result = _eval_delay_rpn(items)
    except (ValueError, ZeroDivisionError, IndexError, KeyError) as exc:
        logger.warning(
            "push_scenario: malformed delay expression %r (%s) — skipping push",
            expr, exc,
        )
        return None
    return max(0.0, result)


async def _resolve_push_delay_seconds(
    delay: object,
    *,
    instance_id: str,
    redis_async: Any | None,
    player_id: str | None = None,
) -> float | None:
    """Resolve a ``push_scenario.delay`` spec into seconds.

    Single-operand forms (suffix literal / ``hh:mm:ss`` / event timer key /
    state-field reference) are resolved by
    :func:`_resolve_push_delay_base_seconds`. When the spec contains an
    arithmetic operator (``+`` / ``-`` / ``*`` / ``/``) it is evaluated as an
    expression — e.g. ``mp_ttl * 2 + 3s`` for "march there and back plus a 3s
    window". (Field/timer-key names never contain these operators.)
    Returns ``None`` (skip the push) when any operand can't resolve, so a missed
    OCR never re-fires on a degenerate delay.
    """
    if delay is None:
        return 0.0
    s = str(delay).strip()
    if not s:
        return 0.0
    if any(op in s for op in ("+", "-", "*", "/")):
        return await _eval_delay_expression(
            s, instance_id=instance_id, redis_async=redis_async, player_id=player_id
        )
    return await _resolve_push_delay_base_seconds(
        s, instance_id=instance_id, redis_async=redis_async, player_id=player_id
    )


async def _resolve_push_expires_at(
    expires: object,
    *,
    instance_id: str,
    redis_async: Any | None,
    player_id: str | None = None,
) -> tuple[float | None, str]:
    """Resolve a ``push_scenario.expires`` spec into an absolute unix deadline.

    Same spec grammar as ``delay`` (suffix literal / ``hh:mm:ss`` / event timer
    key / state field, with arithmetic) — but interpreted as "drop the queued
    item once this much time has passed".

    Returns ``(None, "")`` when no expiry was requested, ``(deadline, "")`` on
    success, and ``(None, <reason>)`` when the push must be skipped: an
    unresolvable spec (missed OCR) or a deadline that has already passed both
    mean the task would be stale, so enqueueing it is pointless.
    """
    if expires is None or not str(expires).strip():
        return None, ""
    secs = await _resolve_push_delay_seconds(
        expires,
        instance_id=instance_id,
        redis_async=redis_async,
        player_id=player_id,
    )
    if secs is None:
        return None, "expires_unresolved"
    if secs <= 0.0:
        return None, "expires_already_passed"
    return time.time() + secs, ""


async def _enqueue_scenario(
    *,
    redis_async: Any | None,
    instance_id: str,
    player_id: str,
    scenario: str,
    priority: int,
    run_at: float,
    skip_if_duplicate: bool,
    expires_at: float | None = None,
) -> bool:
    """Enqueue a DSL scenario as a queue item (task_type = scenario key).

    Thin shim over :meth:`scheduler.queue.RedisQueue.schedule` so DSL
    ``push_scenario`` and exec analyzers share the same atomic dedup
    (Lua ZADD) and ``created_at`` tie-breaker as every other enqueue path.
    A previous hand-rolled implementation did ``ZRANGEBYSCORE`` +
    ``ZADD`` non-atomically, missed the ``created_at`` field, and treated
    a queued device-level item (``player_id=""``) as non-duplicate for a
    player-bound push — which let two equivalent scenarios pile up.
    """
    if redis_async is None:
        return False
    scenario = str(scenario or "").strip()
    player_id = str(player_id or "").strip()
    instance_id = str(instance_id or "").strip()
    if not scenario or not player_id or not instance_id:
        return False

    # Lazy import: ``scheduler.queue`` pulls in ``config.loader`` /
    # ``navigation.screen_graph`` which we don't want to evaluate at
    # ``tasks.*`` import time.
    from scheduler.queue import RedisQueue
    from services import get_settings

    current_screen = ""
    try:
        raw = await redis_async.hget(
            f"wos:instance:{instance_id}:state", "current_screen"
        )
        current_screen = (
            raw.decode() if isinstance(raw, bytes) else str(raw or "")
        ).strip()
    except Exception:
        logger.debug(
            "push_scenario: current_screen read failed instance=%s",
            instance_id,
            exc_info=True,
        )

    queue = RedisQueue(redis_async, get_settings())
    task_id = f"dsl:push:{scenario}:{player_id}:{int(run_at)}"
    enqueued = await queue.schedule(
        task_id=task_id,
        player_id=player_id,
        task_type=scenario,
        priority=int(priority),
        run_at=float(run_at),
        instance_id=instance_id,
        skip_if_duplicate=skip_if_duplicate,
        # Existing helper dedups by (instance_id, player_id, task_type) only;
        # preserve that — there's no region context on a DSL ``push_scenario``.
        dedup_ignore_region=True,
        expires_at=expires_at,
    )
    logger.info(
        "push_scenario enqueue instance=%s current_screen=%r scenario=%s "
        "player=%s priority=%s run_at=%s skip_if_duplicate=%s enqueued=%s task_id=%s",
        instance_id,
        current_screen,
        scenario,
        player_id,
        int(priority),
        float(run_at),
        bool(skip_if_duplicate),
        enqueued,
        task_id,
    )
    return enqueued


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
    except OSError:
        return {}
    return _load_yaml_cached(str(path), st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=512)
def _load_yaml_cached(path_s: str, mtime_ns: int, size: int) -> dict[str, Any]:
    # mtime_ns/size are part of the cache key; they auto-invalidate on file change.
    _ = (mtime_ns, size)
    try:
        raw = yaml.safe_load(Path(path_s).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_area_json(repo_root: Path) -> dict[str, Any]:
    from layout.area_manifest import load_area_doc

    return load_area_doc(repo_root.resolve())


def _collect_ocr_store_targets(steps: Any) -> list[tuple[str, str]]:
    """Walk a scenario step tree and return every ``ocr:`` store target.

    ``store:`` writes to a Redis hash (player or instance scope, defaulting
    to player) — it is documented as scenario-step scoped, so the values
    must NOT persist across runs. This helper enumerates the fields a
    fresh scenario will overwrite so the runner can ``HDEL`` them up front,
    eliminating the "stale ``squad_status`` from the previous fight matches
    the loop's exit cond on iter 0" class of bug.

    Returns a list of ``(scope, field_name)`` pairs. Scope is normalised to
    ``"player"`` or ``"instance"``. ``state:`` paths (long-lived, persisted
    through the SQLite state store) are intentionally NOT included.
    """
    out: list[tuple[str, str]] = []
    if not isinstance(steps, list):
        return out

    for step in steps:
        if not isinstance(step, dict):
            continue

        if "ocr" in step:
            region = str(step.get("ocr") or "").strip()
            raw_store = step.get("store")
            raw_state = step.get("state")
            # Resolve target field — explicit ``store:`` wins, else legacy
            # default ``store: <region>`` applies (only when ``state:`` is
            # also absent, matching ``DslOcrMixin._persist_ocr_result``).
            if raw_store is not None and isinstance(raw_store, str) and raw_store.strip():
                field = raw_store.strip()
            elif raw_store is None and raw_state is None and region:
                field = region
            else:
                field = ""
            if field:
                scope_raw = step.get("scope")
                scope = str(scope_raw).strip().lower() if isinstance(scope_raw, str) else "player"
                if scope not in {"player", "instance"}:
                    scope = "player"
                out.append((scope, field))

        # Recurse into nested steps. Both bare ``steps:`` groups and
        # composite blocks (``loop:`` / ``repeat:`` carrying their own
        # ``steps:``) need walking — store writes can be deeply nested.
        nested = step.get("steps")
        if isinstance(nested, list):
            out.extend(_collect_ocr_store_targets(nested))
        # ``else:`` branches are also executed at runtime (while_match/match
        # fall-through), so store targets buried inside them must be cleared
        # at scenario start too — otherwise the else-branch OCR reads stale
        # values from the previous run.
        else_steps = step.get("else")
        if isinstance(else_steps, list):
            out.extend(_collect_ocr_store_targets(else_steps))
        for spec_key in ("loop", "repeat"):
            spec = step.get(spec_key)
            if isinstance(spec, dict):
                inner = spec.get("steps")
                if isinstance(inner, list):
                    out.extend(_collect_ocr_store_targets(inner))

    return out


_OCR_STORE_SIBLING_SUFFIXES: tuple[str, ...] = ("", "_text", "_confidence", "_at")
"""``DslOcrMixin._persist_ocr_result`` writes 4 fields per ``store:`` target.
Clearing only the bare field leaves the siblings as orphans — explicitly
include all four when wiping at scenario start."""


def _ocr_store_redis_fields(field: str) -> list[str]:
    """Expand a store target name into the 4 Redis hash fields written by
    ``DslOcrMixin._persist_ocr_result``."""
    base = str(field or "").strip()
    if not base:
        return []
    return [f"{base}{suffix}" for suffix in _OCR_STORE_SIBLING_SUFFIXES]


def _parse_wait_seconds(value: object) -> float:
    """Parse a duration string into seconds.

    Accepted forms: raw number (seconds), ``"500ms"``, ``"30s"``, ``"15m"``,
    ``"2h"``. Order of suffix checks matters — ``ms`` must be checked before
    ``s``, and ``m``/``h`` are checked last so they don't shadow them.
    """
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().lower()
    if s.endswith("ms"):
        return float(s[:-2].strip()) / 1000.0
    if s.endswith("s"):
        return float(s[:-1].strip())
    if s.endswith("m"):
        return float(s[:-1].strip()) * 60.0
    if s.endswith("h"):
        return float(s[:-1].strip()) * 3600.0
    return 0.0


def _jittered_wait_seconds(seconds: float, pct: float) -> float:
    """Apply ±pct jitter to a wait duration. ``pct <= 0`` returns ``seconds`` unchanged.

    Used only for the explicit DSL ``wait:`` step — long_click duration and ttl
    must stay exact. Clamped at 0 so negative jitter on tiny waits doesn't go
    below zero.

    The jitter factor is Gaussian (σ = pct/2, so ~95% of draws land inside
    ±pct), clamped to the ±pct bounds: waits cluster around the configured
    duration instead of spreading uniformly, which reads less mechanical.
    """
    if seconds <= 0 or pct <= 0:
        return seconds
    pct = min(pct, 1.0)
    factor = random.gauss(1.0, pct / 2.0)
    factor = min(max(factor, 1.0 - pct), 1.0 + pct)
    return max(0.0, seconds * factor)


def _action_pause_seconds(base_seconds: float = 0.4) -> float:
    """Distribution-based pause between visible actions.

    Most pauses cluster close to ``base_seconds`` with a longer tail, which is
    less mechanical than fixed sleeps while keeping scenario throughput close
    to the previous timing.
    """

    base = max(0.0, float(base_seconds))
    if base <= 0:
        return 0.0
    mode = base
    low = base * 0.55
    high = base * 1.9
    pause = random.triangular(low, high, mode)
    if random.random() < 0.08:
        pause += random.uniform(base * 0.35, base * 1.25)
    return max(0.02, pause)
