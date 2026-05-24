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
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from adb import _redis
from config.paths import repo_root as _repo_root  # noqa: F401

# Re-exported so ``tasks.dsl_scenario`` can pull it through to the test surface,
# where tests monkeypatch ``dsl_scenario._repo_root`` to a tmp_path. The actual
# implementation lives in ``config.paths.repo_root``.

logger = logging.getLogger(__name__)


class _BreakRepeat(Exception):
    """Internal control-flow: break the nearest loop-like block."""


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
#   - `==` / `!=`: case-insensitive full-string match
_COND_TEXT_RE = re.compile(
    r'^\s*(?P<lhs>[\w.\-:]+)\s*(?P<op>==|!=|~=)\s*(?P<rhs>"[^"]*"|\'[^\']*\'|.+?)\s*$'
)
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
    "ocr",
    "exec",
    "click",
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
        "swipe_direction",
        "push_scenario",
        "exec",
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



_HMS_RE = re.compile(r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})")


def _parse_hms_to_seconds(text: str) -> int | None:
    """Parse OCR'd time strings like ``"00:01:23"`` / ``"1:23:45"`` /
    ``"05:30"`` into total seconds. Returns ``None`` when no recognizable
    H:M:S or M:SS group is found.

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
    h_s, m_s, sec_s = m.groups()
    try:
        h = int(h_s) if h_s else 0
        mn = int(m_s)
        sec = int(sec_s)
    except (TypeError, ValueError):
        return None
    if mn >= 60 or sec >= 60:
        return None
    return h * 3600 + mn * 60 + sec


async def _enqueue_scenario(
    *,
    redis_async: Any | None,
    instance_id: str,
    player_id: str,
    scenario: str,
    priority: int,
    run_at: float,
    skip_if_duplicate: bool,
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

    queue = RedisQueue(redis_async, get_settings())
    return await queue.schedule(
        task_id=f"dsl:push:{scenario}:{player_id}:{int(run_at)}",
        player_id=player_id,
        task_type=scenario,
        priority=int(priority),
        run_at=float(run_at),
        instance_id=instance_id,
        skip_if_duplicate=skip_if_duplicate,
        # Existing helper dedups by (instance_id, player_id, task_type) only;
        # preserve that — there's no region context on a DSL ``push_scenario``.
        dedup_ignore_region=True,
    )


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
    """
    if seconds <= 0 or pct <= 0:
        return seconds
    pct = min(pct, 1.0)
    factor = random.uniform(1.0 - pct, 1.0 + pct)
    return max(0.0, seconds * factor)
