"""Optimizer debug API (production + playground)."""
from __future__ import annotations

import dataclasses
from typing import Any

from config.heroes import get_hero_registry
from config.loader import load_settings
from config.state_store import get_state_store
from optimizer import (
    HistoryEntry,
    append_entry,
    apply_command,
    build_envelope,
    compute_capacities,
    enqueue_envelope,
    load_balance_context,
    load_history,
    now_ts,
    plan_top_k,
    rejection_reason,
    solve_optimal,
)
from optimizer.context import BalanceContext, invalidate_balance_context
from optimizer.types import Candidate  # noqa: TC001
from ui.views.optimizer_ui import (
    candidate_table_rows,
    command_label,
    cost_str,
    reasons_for,
)

_DEFAULT_PLAYGROUND_STATE: dict[str, Any] = {
    "chief.furnace_level": 25,
    "account.drill_camp_unlocked": True,
    "resources.hero_xp": 40_000,
    "resources.diamond": 30_000,
    "heroes.entries.molly.available": True,
    "heroes.entries.molly.level": 5,
}


def _ctx_with_profile(profile_id: str | None) -> BalanceContext:
    ctx = load_balance_context()
    if profile_id and profile_id in ctx.profiles:
        return dataclasses.replace(ctx, active_profile_id=profile_id)
    return ctx


def _breakdown_dict(br: Any) -> dict[str, Any]:
    return {
        "final_score": br.final_score,
        "base_value": br.base_value,
        "mode_contributions": dict(br.mode_contributions),
        "threshold_bonus": br.threshold_bonus,
        "replacement_penalty": br.replacement_penalty,
        "resource_rarity_penalty": br.resource_rarity_penalty,
        "notes": list(br.notes),
    }


def _serialize_plan_step(step: Any, ctx: BalanceContext, *, rank: int, status: str) -> dict[str, Any]:
    c = step.candidate
    br = step.breakdown
    detail, from_repr, to_repr = command_label(c)
    return {
        "rank": rank,
        "status": status,
        "candidate_id": c.id,
        "command": c.action,
        "hero": c.hero_id or "",
        "detail": detail,
        "from": from_repr,
        "to": to_repr,
        "cost": cost_str(c),
        "score": round(br.final_score, 1),
        "reasons": ", ".join(reasons_for(c, br, ctx, is_selected=True)),
        "breakdown": _breakdown_dict(br),
    }


def _solve_payload(
    state_flat: dict[str, Any],
    ctx: BalanceContext,
    *,
    server_age_days: int,
    plan_k: int,
) -> dict[str, Any]:
    result, prune, breakdowns = solve_optimal(
        state_flat, ctx, server_age_days=server_age_days
    )
    plan = plan_top_k(state_flat, ctx, k=plan_k, server_age_days=server_age_days)
    capacities = compute_capacities(state_flat, ctx)
    selected_ids = set(result.chosen_ids)
    selected_count = len(result.selected)
    kept_count = len(prune.kept)
    pruned_count = len(prune.dropped)
    rejected_count = kept_count - selected_count
    profile_desc = str(ctx.active_profile.get("description") or "").strip()

    cand_rows = candidate_table_rows(
        prune_kept=prune.kept,
        prune_dropped=prune.dropped,
        breakdowns=breakdowns,
        selected_ids=selected_ids,
        ctx=ctx,
        rejection_reason_fn=rejection_reason,
    )

    plan_rows = [
        _serialize_plan_step(
            step,
            ctx,
            rank=i,
            status="next" if i == 1 else "preview",
        )
        for i, step in enumerate(plan, start=1)
    ]

    spend_by_resource: dict[str, int] = {}
    for c in result.selected:
        for cost in c.costs:
            spend_by_resource[cost.resource] = (
                spend_by_resource.get(cost.resource, 0) + int(cost.amount)
            )
    resource_rows: list[dict[str, Any]] = []
    for resource in sorted(set(capacities) | set(spend_by_resource)):
        available = int(capacities.get(resource, 0))
        spend = int(spend_by_resource.get(resource, 0))
        if available == 0 and spend == 0:
            continue
        resource_rows.append(
            {
                "resource": resource,
                "available": available,
                "selected_spend": spend,
                "remaining": max(0, available - spend),
                "usage_pct": min(1.0, spend / available) if available > 0 else 0.0,
            }
        )

    next_command: dict[str, Any] | None = None
    if plan:
        step = plan[0]
        c = step.candidate
        br = step.breakdown
        detail, from_repr, to_repr = command_label(c)
        env = build_envelope(c, player_id="", instance_id="")
        next_command = {
            "candidate_id": c.id,
            "headline": f"{c.action} · {c.hero_id or '—'}",
            "detail": detail,
            "from": from_repr,
            "to": to_repr,
            "cost": cost_str(c),
            "score": round(br.final_score, 1),
            "band": c.priority_band,
            "reasons": reasons_for(c, br, ctx, is_selected=True),
            "breakdown": _breakdown_dict(br),
            "dispatch": {
                "dsl_scenario": env.dsl_scenario,
                "set_node": env.set_node,
                "region": env.region,
                "task_id": env.task_id,
            },
            "resource_diff": _resource_diff(step.capacities_before, step.capacities_after),
        }

    return {
        "metrics": {
            "status": result.status,
            "objective": result.objective_value,
            "selected_count": selected_count,
            "rejected_count": rejected_count,
            "pruned_count": pruned_count,
            "profile_id": ctx.active_profile_id or "",
            "profile_description": profile_desc,
        },
        "plan": plan_rows,
        "candidates": cand_rows,
        "resources": resource_rows,
        "capacities": {k: int(v) for k, v in capacities.items()},
        "next_command": next_command,
        "pruned": [{"candidate_id": c.id, "reason": reason} for c, reason in prune.dropped],
    }


def _resource_diff(before: dict[str, int], after: dict[str, int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for resource in sorted(set(before) | set(after)):
        b = int(before.get(resource, 0))
        a = int(after.get(resource, 0))
        if b == 0 and a == 0:
            continue
        rows.append(
            {"resource": resource, "before": b, "after": a, "spent": b - a}
        )
    return rows


def _find_candidate(
    state_flat: dict[str, Any],
    ctx: BalanceContext,
    candidate_id: str,
    *,
    server_age_days: int,
) -> tuple[Candidate, Any]:
    _result, prune, breakdowns = solve_optimal(
        state_flat, ctx, server_age_days=server_age_days
    )
    for c in prune.kept:
        if c.id == candidate_id:
            br = breakdowns.get(c.id)
            return c, br
    msg = f"candidate not found: {candidate_id}"
    raise KeyError(msg)


def get_meta() -> dict[str, Any]:
    store = get_state_store()
    gamers = [
        {"id": str(g.id), "nickname": str(g.nickname or "")}
        for g in store._db.gamers
    ]
    settings = load_settings()
    ctx = load_balance_context()
    heroes = [
        {"id": h.id, "name": h.name}
        for h in get_hero_registry().heroes
        if h.id
    ]
    heroes.sort(key=lambda x: x["name"].lower())
    return {
        "gamers": gamers,
        "instances": [i.instance_id for i in settings.instances],
        "profiles": [
            {"id": pid, "description": str((prof or {}).get("description") or "")}
            for pid, prof in ctx.profiles.items()
        ],
        "active_profile_id": ctx.active_profile_id or "",
        "heroes": heroes,
        "default_playground_state": _DEFAULT_PLAYGROUND_STATE,
    }


def reload_balance() -> dict[str, str]:
    invalidate_balance_context()
    return {"ok": True}


def solve(
    *,
    mode: str,
    gamer_id: str | None = None,
    state_flat: dict[str, Any] | None = None,
    server_age_days: int = 14,
    plan_k: int = 8,
    profile_id: str | None = None,
) -> dict[str, Any]:
    ctx = _ctx_with_profile(profile_id)
    if mode == "production":
        if not gamer_id:
            msg = "gamer_id required for production mode"
            raise ValueError(msg)
        store = get_state_store()
        gamer = next((g for g in store._db.gamers if str(g.id) == gamer_id), None)
        if gamer is None:
            msg = f"unknown gamer: {gamer_id}"
            raise KeyError(msg)
        state = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()
        payload = _solve_payload(state, ctx, server_age_days=server_age_days, plan_k=plan_k)
        payload["gamer_id"] = gamer_id
        payload["state_flat"] = state
        return payload
    if mode == "playground":
        state = dict(state_flat or _DEFAULT_PLAYGROUND_STATE)
        return _solve_payload(state, ctx, server_age_days=server_age_days, plan_k=plan_k)
    msg = f"unknown mode: {mode}"
    raise ValueError(msg)


def dry_run(
    *,
    gamer_id: str | None,
    state_flat: dict[str, Any] | None,
    candidate_id: str,
    server_age_days: int = 14,
    profile_id: str | None = None,
) -> dict[str, Any]:
    ctx = _ctx_with_profile(profile_id)
    if gamer_id:
        store = get_state_store()
        gamer = next((g for g in store._db.gamers if str(g.id) == gamer_id), None)
        if gamer is None:
            msg = f"unknown gamer: {gamer_id}"
            raise KeyError(msg)
        state = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()
    else:
        state = dict(state_flat or _DEFAULT_PLAYGROUND_STATE)
    cand, _br = _find_candidate(
        state, ctx, candidate_id, server_age_days=server_age_days
    )
    new_flat = apply_command(state, cand)
    changed = {
        k: {"before": state.get(k), "after": v}
        for k, v in new_flat.items()
        if state.get(k) != v
    }
    return {"changed_keys": len(changed), "diff": changed}


def approve(
    *,
    gamer_id: str,
    candidate_id: str,
    server_age_days: int = 14,
    profile_id: str | None = None,
) -> dict[str, Any]:
    ctx = _ctx_with_profile(profile_id)
    store = get_state_store()
    gamer = next((g for g in store._db.gamers if str(g.id) == gamer_id), None)
    if gamer is None:
        msg = f"unknown gamer: {gamer_id}"
        raise KeyError(msg)
    state_flat = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()
    cand, br = _find_candidate(
        state_flat, ctx, candidate_id, server_age_days=server_age_days
    )
    new_flat = apply_command(state_flat, cand)
    full_diff = {
        k: (state_flat.get(k), v) for k, v in new_flat.items() if state_flat.get(k) != v
    }
    if full_diff:
        persistable = {k: new_flat[k] for k in full_diff}
        gamer_store = store.get_or_create(str(gamer.id), nickname=gamer.nickname)
        gamer_store.update_from_flat(persistable)
    reasons = reasons_for(cand, br, ctx, is_selected=True)
    append_entry(
        HistoryEntry(
            approved_at=now_ts(),
            gamer_id=str(gamer.id),
            profile=ctx.active_profile_id,
            candidate_id=cand.id,
            action=cand.action,
            hero_id=cand.hero_id,
            score=float(br.final_score),
            costs=[{"resource": x.resource, "amount": int(x.amount)} for x in cand.costs],
            state_diff={
                k: {"before": before, "after": after}
                for k, (before, after) in full_diff.items()
            },
            reasons=reasons,
            notes=list(br.notes),
        )
    )
    return {"ok": True, "persisted_keys": len(full_diff)}


def queue_for_bot(
    *,
    instance_id: str,
    gamer_id: str,
    candidate_id: str,
    server_age_days: int = 14,
    profile_id: str | None = None,
) -> dict[str, Any]:
    ctx = _ctx_with_profile(profile_id)
    store = get_state_store()
    gamer = next((g for g in store._db.gamers if str(g.id) == gamer_id), None)
    if gamer is None:
        msg = f"unknown gamer: {gamer_id}"
        raise KeyError(msg)
    state_flat = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()
    cand, _br = _find_candidate(
        state_flat, ctx, candidate_id, server_age_days=server_age_days
    )
    env = build_envelope(cand, player_id=str(gamer.id), instance_id=instance_id)
    from api.deps import get_redis

    qk = enqueue_envelope(env, get_redis())
    return {
        "ok": True,
        "queue_key": qk,
        "task_id": env.task_id,
        "dsl_scenario": env.dsl_scenario,
    }


def list_history(*, gamer_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    rows = list(reversed(load_history()))
    if gamer_id:
        rows = [h for h in rows if h.gamer_id == gamer_id]
    rows = rows[:limit]
    return {
        "entries": [
            {
                "approved_at": h.approved_at,
                "gamer_id": h.gamer_id,
                "profile": h.profile,
                "candidate_id": h.candidate_id,
                "action": h.action,
                "hero_id": h.hero_id,
                "score": round(h.score, 1),
                "costs": h.costs,
            }
            for h in rows
        ]
    }
