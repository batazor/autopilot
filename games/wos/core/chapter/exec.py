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
from contextlib import suppress
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


# ---------------------------------------------------------------------------
# Idle Chapter-objective router
#
# When the instance is otherwise idle, ``advance_chapter_objective`` OCRs the
# bottom-left Chapter objective tracker (``chapter.task`` on main_city) into
# player state and calls ``route_chapter_objective``. Building objectives
# ("Upgrade/Build <X>") are resolved by name and gated by the build planner
# before handing off to ``building.upgrade``; everything else routes through the
# ``chapter_objectives.yaml`` registry, mirroring the daily-mission router.
# ---------------------------------------------------------------------------
_OBJECTIVES_PATH = Path(__file__).resolve().parent / "chapter_objectives.yaml"
_CHAPTER_TASK_FIELD = "chapter.task"
# Idle-tier: above the trigger cron (25k) so the routed work runs before the
# next idle tick, but below every real cron/overlay push (68k+).
_OBJECTIVE_PUSH_PRIORITY = 40_000


@lru_cache(maxsize=4)
def _load_objectives_cached(path_str: str, _mtime_ns: int) -> tuple[_MissionRule, ...]:
    try:
        doc = yaml.safe_load(Path(path_str).read_text(encoding="utf-8")) or {}
    except OSError:
        logger.warning("chapter_objectives: registry unreadable at %s", path_str)
        return ()
    out: list[_MissionRule] = []
    for entry in doc.get("objectives") or []:
        if not isinstance(entry, dict):
            continue
        raw_pat = str(entry.get("pattern") or "").strip()
        if not raw_pat:
            continue
        try:
            compiled = re.compile(raw_pat, re.IGNORECASE)
        except re.error:
            logger.exception("chapter_objectives: bad regex %r — skipping", raw_pat)
            continue
        raw_scenario = entry.get("scenario")
        scenario = str(raw_scenario).strip() if raw_scenario else None
        raw_args = entry.get("args")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        out.append((compiled, scenario, args))
    return tuple(out)


def _load_objectives() -> tuple[_MissionRule, ...]:
    try:
        st = _OBJECTIVES_PATH.stat()
    except OSError:
        logger.warning("chapter_objectives: registry not found at %s", _OBJECTIVES_PATH)
        return ()
    return _load_objectives_cached(str(_OBJECTIVES_PATH), st.st_mtime_ns)


def _read_levels(state: Any) -> dict[str, int]:
    """Pull ``buildings.levels.<slug>`` ints out of an instance-state hash.

    Mirrors ``building/common/exec.py::_read_levels`` (kept local — the build
    exec module is auto-discovered, not import-stable as a package path).
    """
    prefix = "buildings.levels."
    out: dict[str, int] = {}
    for raw_k, raw_v in (state or {}).items():
        k = raw_k.decode() if isinstance(raw_k, bytes) else str(raw_k)
        if not k.startswith(prefix):
            continue
        v = raw_v.decode() if isinstance(raw_v, bytes) else str(raw_v)
        try:
            out[k[len(prefix):]] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def _match_building_in_text(text: str, buildings: Any) -> Any | None:
    """Resolve the building named *inside* an objective line (e.g. "Upgrade Coal
    Mine to Lv. 5" → the Coal Mine ``BuildingDef``).

    The objective text wraps the building name in verbs/levels, so the exact
    ``building_by_ocr_name`` match won't fire — we look for any registry name (EN
    or its RU localisation) as a contiguous run of *lemmas*, longest match wins
    (so "Coal Mine" beats a stray short token). RU-aware via
    ``ru_aliases_for_building``.

    Declension-tolerant: a RU objective declines the building name to the
    accusative ("улучшите **Кухню**", not the nominative «Кухня»). pymorphy3
    lemmatises every case form back to «кухня», so the lemma run «кухня» is found
    in «кухня улучшить …» regardless of the surface ending — «Кухня»→«Кухню»,
    «Столовая»→«Столовую», «Лесопилка»→«Лесопилку» all match. EN names lemmatise
    to themselves, so they match exactly as before. See
    ``config.building_name_parser.lemma_phrase_in_text``.
    """
    from config.building_name_parser import (
        lemma_phrase_in_text,
        ru_aliases_for_building,
        ru_lemma_tokens,
    )

    if not (text or "").strip():
        return None
    best = None
    best_len = 0
    for b in buildings:
        for nm in (b.name, *ru_aliases_for_building(b.name)):
            if not lemma_phrase_in_text(nm, text):
                continue
            # Rank by the lemma-token character length so a longer registry name
            # (more specific) beats a short one when several match.
            score = sum(len(t) for t in ru_lemma_tokens(nm))
            if score > best_len:
                best, best_len = b, score
    return best


def _route_chapter_objective(
    text: str,
    *,
    levels: Any,
    graph: Any,
    buildings: Any,
    objectives: tuple[_MissionRule, ...],
) -> dict[str, Any]:
    """Pure router for the single Chapter objective → a decision dict.

    Building objectives are gated by the build planner; when no anchor level has
    been read yet (``furnace`` absent), the planner can't judge feasibility, so
    we defer to the in-game Upgrade/Build button inside ``building.upgrade``
    rather than skip. Non-building objectives match the registry. Pure (no I/O),
    unit-tested.
    """
    out: dict[str, Any] = {
        "kind": "none", "building": None, "scenario": None, "args": {},
        "feasible": False, "reason": "", "matched": "", "text": (text or "").strip(),
    }
    if not out["text"]:
        out["reason"] = "empty"
        return out

    bdef = _match_building_in_text(out["text"], buildings)
    if bdef is not None:
        from games.wos.core.building.planner import GOAL_UNKNOWN, plan_next

        plan = plan_next(graph, levels, goal_id=bdef.id)
        step = plan.step
        # The planner can't judge feasibility when no anchor level has been read
        # (an unread furnace reads as level 0, blocking everything) OR the building
        # isn't modelled in the graph — in both cases defer to the in-game
        # Upgrade/Build button inside building.upgrade rather than skip.
        blind = "furnace" not in (levels or {})
        out.update({"kind": "building", "building": bdef.id})
        if step is not None and step.building_id == bdef.id:
            # The objective building itself is the ready next step.
            if plan.affordable:
                out.update({"scenario": "building.upgrade", "feasible": True, "reason": plan.reason})
            else:
                out.update({"scenario": None, "feasible": False, "reason": "insufficient_resources"})
        elif blind or plan.reason == GOAL_UNKNOWN:
            out.update(
                {"scenario": "building.upgrade", "feasible": True,
                 "reason": "planner_blind_fallback" if blind else "planner_unknown_fallback"}
            )
        elif step is not None:
            # The planner would advance a prerequisite first → the objective
            # building isn't directly upgradeable; tapping chapter.task lands on
            # it and the upgrade loop would no-op, so skip.
            out.update({"scenario": None, "feasible": False, "reason": "prereq_pending"})
        else:
            out.update({"scenario": None, "feasible": False, "reason": plan.reason or "not_upgradeable"})
        return out

    for pattern, scenario, arg_spec in objectives:
        m = pattern.search(out["text"])
        if not m:
            continue
        out["matched"] = m.group(0).strip()
        if scenario is None:
            out.update({"kind": "scenario", "reason": "unautomated"})
            return out
        out.update(
            {"kind": "scenario", "scenario": scenario, "args": _resolve_args(arg_spec, m),
             "feasible": True, "reason": "routed"}
        )
        return out

    out["reason"] = "unrecognised"
    return out


async def _exec_route_chapter_objective(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec route_chapter_objective: no redis client")
        ctx.result.update({"reason": "no_redis_client"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec route_chapter_objective: empty player_id")
        ctx.result.update({"reason": "empty_player_id"})
        return

    text = _decode_redis_raw(
        await ctx.redis_client.hget(f"wos:player:{player_id}:state", _CHAPTER_TASK_FIELD)
    )
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        state = await ctx.redis_client.hgetall(inst_key)
    except Exception:
        state = {}
    levels = _read_levels(state)

    from games.wos.core.building.planner import load_graph

    from config.buildings import get_building_registry

    decision = _route_chapter_objective(
        text,
        levels=levels,
        graph=load_graph(),
        buildings=get_building_registry().buildings,
        objectives=_load_objectives(),
    )

    # Observability flags (botctl state / why).
    flags = {
        "chapter.objective.text": str(decision.get("text") or ""),
        "chapter.objective.kind": str(decision.get("kind") or ""),
        "chapter.objective.building": str(decision.get("building") or ""),
        "chapter.objective.scenario": str(decision.get("scenario") or ""),
        "chapter.objective.feasible": "1" if decision.get("feasible") else "0",
        "chapter.objective.reason": str(decision.get("reason") or ""),
    }
    with suppress(Exception):
        await ctx.redis_client.hset(inst_key, mapping=flags)

    scenario = decision.get("scenario")
    pushed = False
    if scenario:
        # Lazy import (scheduler/queue) — same reason as route_daily_missions.
        from tasks.dsl_scenario_helpers import _enqueue_scenario

        pushed = await _enqueue_scenario(
            redis_async=ctx.redis_client,
            instance_id=ctx.instance_id,
            player_id=player_id,
            scenario=scenario,
            priority=_OBJECTIVE_PUSH_PRIORITY,
            run_at=time.time(),
            skip_if_duplicate=True,
            args=decision.get("args") or None,
        )

    ctx.result.update(
        {
            "action": "routed",
            "kind": decision.get("kind"),
            "building": decision.get("building"),
            "scenario": scenario or "",
            "feasible": bool(decision.get("feasible")),
            "reason": decision.get("reason"),
            "pushed": pushed,
        }
    )
    logger.info(
        "route_chapter_objective: player=%s kind=%s building=%s scenario=%s feasible=%s "
        "reason=%s pushed=%s text=%r",
        player_id,
        decision.get("kind"),
        decision.get("building"),
        scenario,
        decision.get("feasible"),
        decision.get("reason"),
        pushed,
        decision.get("text"),
    )


DSL_EXEC_HANDLERS = {
    "route_daily_missions": _exec_route_daily_missions,
    "route_chapter_objective": _exec_route_chapter_objective,
}
