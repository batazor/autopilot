"""DSL exec: route the daily-mission list to per-mission automations.

``route_daily_missions`` reads the accumulated daily-mission text that
``chapter.claim_missions`` OCR'd into ``chapter.daily.tasks`` (player state),
matches each line against the declarative registry in ``daily_missions.yaml``,
and pushes the matching automation scenario with the parsed values as ``args``
and an expiry of ``chapter.daily.refresh - 10m`` (a stale mission is pointless
after the daily reset). Missions with no automation yet (``scenario: null``) are
logged, not pushed — so they stay documented and easy to wire later.

The parse/route core (:func:`_route_missions` / :func:`_resolve_args`) is pure
so it can be unit-tested without Redis — see ``tests/test_daily_missions_router``.
"""
from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent / "daily_missions.yaml"
_TASKS_FIELD = "chapter.daily.tasks"
# Same expiry the hand-written routing used: drop a mission push 10 minutes
# before the game-day reset (it's moot once the list rolls over).
_REFRESH_EXPIRES = "chapter.daily.refresh - 10m"
_PRIORITY = 80_000
_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")

# A compiled registry entry: (pattern, scenario_or_None, args). Kept as a plain
# tuple — this module is imported via importlib without a sys.modules entry, and
# @dataclass / NamedTuple introspection (``cls.__module__`` lookup) fails under
# that loader.
_MissionRule = tuple[re.Pattern[str], "str | None", dict[str, Any]]


@lru_cache(maxsize=4)
def _load_registry_cached(path_str: str, _mtime_ns: int) -> tuple[_MissionRule, ...]:
    try:
        doc = yaml.safe_load(Path(path_str).read_text(encoding="utf-8")) or {}
    except OSError:
        logger.warning("daily_missions: registry unreadable at %s", path_str)
        return ()
    out: list[_MissionRule] = []
    for entry in doc.get("missions") or []:
        if not isinstance(entry, dict):
            continue
        raw_pat = str(entry.get("pattern") or "").strip()
        if not raw_pat:
            continue
        try:
            compiled = re.compile(raw_pat, re.IGNORECASE)
        except re.error:
            logger.exception("daily_missions: bad regex %r — skipping", raw_pat)
            continue
        raw_scenario = entry.get("scenario")
        scenario = str(raw_scenario).strip() if raw_scenario else None
        raw_args = entry.get("args")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        out.append((compiled, scenario, args))
    return tuple(out)


def _load_registry() -> tuple[_MissionRule, ...]:
    try:
        st = _REGISTRY_PATH.stat()
    except OSError:
        logger.warning("daily_missions: registry not found at %s", _REGISTRY_PATH)
        return ()
    return _load_registry_cached(str(_REGISTRY_PATH), st.st_mtime_ns)


def _coerce(value: str) -> Any:
    """All-digit values (thousands separators stripped) become ints; else str."""
    digits = value.replace(",", "").strip()
    return int(digits) if digits.isdigit() else value


def _resolve_args(arg_spec: dict[str, Any], match: re.Match[str]) -> dict[str, Any]:
    """Resolve an entry's ``args`` against a regex match (``${group}`` → value)."""
    groups = match.groupdict()
    out: dict[str, Any] = {}
    for key, raw in arg_spec.items():
        if isinstance(raw, str):
            substituted = _PLACEHOLDER_RE.sub(
                lambda m: str(groups.get(m.group(1)) or ""), raw
            )
            out[key] = _coerce(substituted)
        else:
            out[key] = raw
    return out


def _route_missions(
    buffer: str, registry: tuple[_MissionRule, ...]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pure router: ``(pushes, unautomated)``.

    ``pushes`` is a list of ``{"scenario": str, "args": dict}`` de-duplicated by
    the resolved ``(scenario, args)`` pair. ``unautomated`` is the matched text
    of recognised-but-not-yet-automated missions (``scenario: null``).
    """
    pushes: list[dict[str, Any]] = []
    unautomated: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for pattern, scenario, arg_spec in registry:
        for m in pattern.finditer(buffer):
            if scenario is None:
                unautomated.append(m.group(0).strip())
                continue
            args = _resolve_args(arg_spec, m)
            key = (scenario, tuple(sorted(args.items())))
            if key in seen:
                continue
            seen.add(key)
            pushes.append({"scenario": scenario, "args": args})
    return pushes, unautomated


async def _exec_route_daily_missions(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec route_daily_missions: no redis client")
        ctx.result.update({"reason": "no_redis_client"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec route_daily_missions: empty player_id")
        ctx.result.update({"reason": "empty_player_id"})
        return

    buffer = _decode_redis_raw(
        await ctx.redis_client.hget(f"wos:player:{player_id}:state", _TASKS_FIELD)
    )
    if not buffer.strip():
        ctx.result.update({"action": "empty_buffer"})
        return

    pushes, unautomated = _route_missions(buffer, _load_registry())

    # Lazy import: tasks.dsl_scenario_helpers pulls in scheduler/queue, which we
    # don't want evaluated at module import (exec.py loads at registry build).
    from tasks.dsl_scenario_helpers import _enqueue_scenario, _resolve_push_expires_at

    expires_at, expires_skip = await _resolve_push_expires_at(
        _REFRESH_EXPIRES,
        instance_id=ctx.instance_id,
        redis_async=ctx.redis_client,
        player_id=player_id,
    )
    # The refresh timer is written by the OCR step that runs before this exec, so
    # an unresolvable expiry is a transient miss — push without one rather than
    # suppressing the whole day's automation (the expiry is an optimisation, not
    # correctness).
    if expires_skip:
        logger.info(
            "dsl exec route_daily_missions: expiry unresolved (%s) — pushing "
            "without expiry player=%s",
            expires_skip,
            player_id,
        )
        expires_at = None

    pushed: list[str] = []
    now = time.time()
    for push in pushes:
        ok = await _enqueue_scenario(
            redis_async=ctx.redis_client,
            instance_id=ctx.instance_id,
            player_id=player_id,
            scenario=push["scenario"],
            priority=_PRIORITY,
            run_at=now,
            skip_if_duplicate=True,
            expires_at=expires_at,
            args=push["args"] or None,
        )
        if ok:
            pushed.append(push["scenario"])

    if unautomated:
        logger.info(
            "dsl exec route_daily_missions: %d mission(s) without automation yet: %s",
            len(unautomated),
            "; ".join(unautomated),
        )
    ctx.result.update(
        {
            "action": "routed",
            "pushed": pushed,
            "pushed_count": len(pushed),
            "unautomated_count": len(unautomated),
        }
    )
    logger.info(
        "dsl exec route_daily_missions: player=%s pushed=%s unautomated=%d",
        player_id,
        pushed,
        len(unautomated),
    )


DSL_EXEC_HANDLERS = {
    "route_daily_missions": _exec_route_daily_missions,
}
