"""Stateless helpers for the DSL scenario executor.

Pulled out of ``tasks/dsl_scenario.py`` so the main file stays focused on the
``DslScenarioTask`` runtime. Everything here is pure functions over plain
inputs (dicts, strings, paths, redis client) — no class state.

External callers should still import ``DslScenarioTask`` from
``tasks.dsl_scenario``; this module is internal.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from actions.tap import _redis

logger = logging.getLogger(__name__)


class _BreakRepeat(Exception):
    """Internal control-flow: break the nearest loop-like block."""


def _step_red_dot_requirement(step: dict[str, Any]) -> bool | None:
    """Read optional ``isRedDot`` predicate on a ``match:`` / ``while_match:`` step.

    Accepts the YAML-natural form ``isRedDot: true|false`` and a few common string
    aliases (``yes/no/on/off``) for resilience. Returns ``None`` when the field is
    absent or unparseable — so ``match:`` behaves exactly as before for every step
    that does not opt in.
    """
    if not isinstance(step, dict):
        return None
    if "isRedDot" in step:
        raw = step.get("isRedDot")
    elif "is_red_dot" in step:
        raw = step.get("is_red_dot")
    else:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in {"true", "yes", "y", "1", "on"}:
            return True
        if s in {"false", "no", "n", "0", "off"}:
            return False
    return None


# ---------------------------------------------------------------------------
# Color checks (dominant color in a bbox)
# ---------------------------------------------------------------------------

_COLOR_WORD_ALIASES: dict[str, str] = {
    "red": "red",
    "blue": "blue",
    "gray": "gray",
    "grey": "gray",
    "green": "green",
    "красный": "red",
    "синий": "blue",
    "серый": "gray",
    "зелёный": "green",
    "зеленый": "green",
}

# Simple guard for DSL steps, e.g. ``cond: currentNode != main_city`` (skip when false).
_COND_SCREEN_RE = re.compile(
    r"^\s*(?P<lhs>[\w]+)\s*(?P<op>==|!=)\s*(?P<rhs>[\w.-]+)\s*$",
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

# ``loop`` / ``repeat`` / ``while_match`` also nest ``steps``; composite blocks use only ``cond`` + ``steps``.
_DSL_STEP_ACTION_KEYS = frozenset({
    "match",
    "while_match",
    "repeat",
    "loop",
    "push_scenario",
    "swipe_direction",
    "ocr",
    "exec",
    "set_node",
    "click",
    "wait",
})


def _dsl_step_summary(step: Any) -> str:
    """Short human-readable label for queue/history step traces."""
    if not isinstance(step, dict):
        return "(invalid)"
    for key in (
        "click",
        "match",
        "while_match",
        "ocr",
        "set_node",
        "swipe_direction",
        "push_scenario",
        "exec",
        "wait",
        "repeat",
        "loop",
    ):
        if key not in step:
            continue
        val = step[key]
        if key in ("click", "match", "while_match", "ocr", "set_node"):
            s = str(val).strip()
            return f"{key}:{s[:48]}{'…' if len(s) > 48 else ''}"
        if key == "repeat":
            return "repeat"
        if key == "loop":
            return "loop"
        if key == "swipe_direction":
            return f"swipe:{str(val)[:40]}"
        if key == "push_scenario":
            return f"push:{str(val)[:40]}"
        if key == "exec":
            return f"exec:{str(val)[:40]}"
        if key == "wait":
            return f"wait:{str(val)[:24]}"
    if "steps" in step and isinstance(step.get("steps"), list):
        return f"group({len(step['steps'])})"
    extra = [k for k in step if k != "cond"]
    return ",".join(extra[:5]) or "(empty)"


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


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
    """Enqueue a DSL scenario as a queue item (task_type = scenario key)."""
    if redis_async is None:
        return False
    scenario = str(scenario or "").strip()
    player_id = str(player_id or "").strip()
    instance_id = str(instance_id or "").strip()
    if not scenario or not player_id or not instance_id:
        return False

    # Optional duplicate guard: same (player, task_type) already queued.
    if skip_if_duplicate:
        try:
            items = await redis_async.zrangebyscore(
                f"wos:queue:{instance_id}" if instance_id else "wos:queue:unknown",
                "-inf",
                "+inf",
            )
            for raw in items:
                try:
                    payload = raw.decode() if isinstance(raw, bytes) else str(raw)
                    doc = json.loads(payload)
                    if (
                        str(doc.get("player_id") or "") == player_id
                        and str(doc.get("task_type") or "") == scenario
                    ):
                        return False
                except Exception:
                    continue
        except Exception:
            # If we can't check, still allow enqueue.
            pass

    body: dict[str, object] = {
        "task_id": f"dsl:push:{scenario}:{player_id}:{int(run_at)}",
        "player_id": player_id,
        "task_type": scenario,
        "priority": int(priority),
        "run_at": float(run_at),
        "instance_id": instance_id,
    }
    qkey = f"wos:queue:{instance_id}" if instance_id else "wos:queue:unknown"
    await redis_async.zadd(qkey, {json.dumps(body): float(run_at)})
    return True


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
    p = repo_root / "area.json"
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))  # JSON is valid YAML
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_wait_seconds(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().lower()
    if s.endswith("ms"):
        return float(s[:-2].strip()) / 1000.0
    if s.endswith("s"):
        return float(s[:-1].strip())
    return 0.0
