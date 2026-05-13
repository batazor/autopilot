"""Optimizer debug UI — MVP layout per ``wos_optimizer_ui_final.md``.

A metric strip at the top (status / objective / selected / rejected /
profile / generation) feeds into five tabs:

* **Command Queue** — top-K execution plan (re-solve after each).
* **Next Command** — single-step view with Dry run / Approve buttons.
* **Candidates** — every scored candidate, selected/rejected/pruned status.
* **Resources** — available / spend / reserve / remaining / usage_pct.
* **Score Breakdown** — bar chart of components for the selected card.

The page calls ``solve_optimal`` + ``plan_top_k`` directly (no
intermediate ``solver_result.yaml`` file) — fine while the solver is
fast enough to inline; we'll split into a background process when that
stops being true.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from config.loader import load_settings
from config.state_store import get_state_store
from optimizer import (
    HistoryEntry,
    append_entry,
    apply_command,
    build_envelope,
    compute_capacities,
    enqueue_envelope,
    generate_reasons,
    load_balance_context,
    load_history,
    now_ts,
    plan_top_k,
    rejection_reason,
    solve_optimal,
)
from optimizer.context import invalidate_balance_context
from optimizer.scorer import ScoreBreakdown
from optimizer.types import Candidate
from ui.redis_client import get_redis

st.title("Optimizer · production debug panel")
st.caption(
    "Reads `config/balance/*.yaml` + `db/state.yaml`, runs CP-SAT, shows "
    "what the bot would do, why, and what gets dropped. Answers: *what "
    "next?* / *why this?* / *why not the rest?*"
)


# --- Pick gamer + tunables ------------------------------------------------
col_gamer, col_inst, col_age, col_horizon, col_refresh = st.columns(
    [2, 1, 1, 1, 1], vertical_alignment="bottom"
)

store = get_state_store()
db = store._db
gamers = list(db.gamers)
if not gamers:
    st.warning("`db/state.yaml` has no gamers yet. Add one first.")
    st.stop()

with col_gamer:
    gamer_idx = st.selectbox(
        "Gamer",
        options=range(len(gamers)),
        format_func=lambda i: f"{gamers[i].nickname or '(no nick)'} · `{gamers[i].id}`",
        key="optimizer_gamer",
    )
    gamer = gamers[gamer_idx]

with col_inst:
    settings = load_settings()
    instance_ids = [inst.instance_id for inst in settings.instances]
    if instance_ids:
        instance_id = st.selectbox(
            "Instance",
            options=instance_ids,
            key="optimizer_instance",
        )
    else:
        st.caption("no instances")
        instance_id = ""

with col_age:
    server_age = int(
        st.number_input(
            "server_age_days",
            min_value=0,
            max_value=400,
            value=14,
            step=1,
            key="optimizer_age",
        )
    )

with col_horizon:
    plan_k = int(
        st.number_input(
            "Plan K",
            min_value=1,
            max_value=20,
            value=8,
            step=1,
            key="optimizer_plan_k",
            help="How many ``top-1 → apply → re-solve`` steps to preview.",
        )
    )

with col_refresh:
    if st.button("🔄 Reload balance", use_container_width=True):
        invalidate_balance_context()
        st.success("Balance cache cleared.")


ctx = load_balance_context()
state_flat = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()


# --- One-shot solve + plan ------------------------------------------------

result, prune, breakdowns = solve_optimal(state_flat, ctx, server_age_days=server_age)
plan = plan_top_k(state_flat, ctx, k=plan_k, server_age_days=server_age)
capacities = compute_capacities(state_flat, ctx)

selected_ids = set(result.chosen_ids)
selected_count = len(result.selected)
kept_count = len(prune.kept)
pruned_count = len(prune.dropped)
rejected_count = kept_count - selected_count


# --- Dashboard strip ------------------------------------------------------

m1, m2, m3, m4, m5, m6 = st.columns(6)
status_emoji = "🟢" if result.status in ("OPTIMAL", "FEASIBLE") else "🟠"
m1.metric("Solver", f"{status_emoji} {result.status}")
m2.metric("Objective", f"{result.objective_value:,}")
m3.metric("Selected", selected_count)
m4.metric("Rejected", rejected_count)
m5.metric("Pruned", pruned_count)
m6.metric("Profile", ctx.active_profile_id or "(none)")

_profile_desc = str(ctx.active_profile.get("description") or "").strip()
if _profile_desc:
    st.caption(f"📌 {_profile_desc}")


# --- Helpers --------------------------------------------------------------


def _command_label(c: Candidate) -> tuple[str, str, str]:
    """Return ``(detail, from_repr, to_repr)`` for a candidate."""
    detail = ""
    from_disp = c.payload.get("from_level")
    to_disp = c.payload.get("to_level")
    if c.action == "star_tier_up":
        from_disp = c.payload.get("from_progress")
        to_disp = c.payload.get("to_progress")
        detail = f"★{c.payload.get('star_level')} t{c.payload.get('tier_in_star')}"
    elif c.action == "skill_up":
        detail = f"{c.payload.get('track')}.{c.payload.get('slot')}"
    return detail, "" if from_disp is None else str(from_disp), "" if to_disp is None else str(to_disp)


def _cost_str(c: Candidate) -> str:
    return ", ".join(f"{cost.amount} {cost.resource}" for cost in c.costs) or "—"


def _reasons_for(c: Candidate, br: ScoreBreakdown, *, is_selected: bool | None = None) -> list[str]:
    return generate_reasons(c, br, ctx, is_selected=is_selected)


# --- Tabs -----------------------------------------------------------------

(
    tab_queue,
    tab_next,
    tab_cand,
    tab_res,
    tab_score,
    tab_constr,
    tab_history,
    tab_raw,
) = st.tabs(
    [
        "Command Queue",
        "Next Command",
        "Candidates",
        "Resources",
        "Score Breakdown",
        "Constraints",
        "History",
        "Raw",
    ]
)


# ---------- Command Queue ---------------------------------------------------
with tab_queue:
    st.caption(
        f"Top-{plan_k} preview: simulated `solve → apply top 1 → re-solve` "
        f"loop. **Only step #1 is safe to execute** — later steps assume the "
        f"earlier ones already ran."
    )
    if not plan:
        st.info("Planner returned no steps — capacities probably 0 for all required resources.")
    else:
        queue_rows: list[dict[str, Any]] = []
        for i, step in enumerate(plan, start=1):
            c = step.candidate
            br = step.breakdown
            detail, from_repr, to_repr = _command_label(c)
            queue_rows.append(
                {
                    "#": i,
                    "command": c.action,
                    "hero": c.hero_id or "",
                    "detail": detail,
                    "from": from_repr,
                    "to": to_repr,
                    "cost": _cost_str(c),
                    "score": round(br.final_score, 1),
                    "reasons": ", ".join(_reasons_for(c, br, is_selected=True)),
                    "status": "step 1 — pending" if i == 1 else "preview",
                }
            )
        st.dataframe(pd.DataFrame(queue_rows), width="stretch", hide_index=True)


# ---------- Next Command ----------------------------------------------------
with tab_next:
    if not plan:
        st.warning("No executable next command — solver found nothing.")
    else:
        step = plan[0]
        c = step.candidate
        br = step.breakdown
        detail, from_repr, to_repr = _command_label(c)
        reasons = _reasons_for(c, br, is_selected=True)
        st.info(
            f"**{c.action}** · `{c.hero_id or ''}`"
            + (f" · {detail}" if detail else "")
            + (f" · {from_repr} → {to_repr}" if from_repr else "")
        )
        ca, cb = st.columns(2)
        with ca:
            st.markdown(
                f"**Score:** `{br.final_score:.1f}` "
                f"(base `{br.base_value:.1f}` · gain ×`{br.upgrade_gain:.2f}`"
                + (f" · threshold +`{br.threshold_bonus:.0f}`" if br.threshold_bonus else "")
                + f" · −penalty `{br.replacement_penalty + br.resource_rarity_penalty:.1f}`)"
            )
            st.markdown(f"**Cost:** `{_cost_str(c)}`")
            st.markdown(f"**Priority band:** `{c.priority_band}`")
            st.markdown("**Reasons:**")
            for r in reasons:
                st.markdown(f"- `{r}`")
            if c.preconditions:
                with st.expander("Preconditions", expanded=False):
                    for p in c.preconditions:
                        st.markdown(f"- {p}")
        with cb:
            st.markdown("**Resource diff after this step**")
            diff_rows = []
            for resource in sorted(set(step.capacities_before) | set(step.capacities_after)):
                before = step.capacities_before.get(resource, 0)
                after = step.capacities_after.get(resource, 0)
                if before == 0 and after == 0:
                    continue
                diff_rows.append(
                    {
                        "resource": resource,
                        "before": before,
                        "after": after,
                        "spent": before - after,
                    }
                )
            if diff_rows:
                st.dataframe(pd.DataFrame(diff_rows), width="stretch", hide_index=True)
            else:
                st.caption("No tracked resources change.")

        st.divider()
        env_preview = build_envelope(
            c, player_id=str(gamer.id), instance_id=instance_id or "(default)"
        )
        st.caption(
            f"Dispatch target: scenario `{env_preview.dsl_scenario}` · "
            f"node `{env_preview.set_node}`"
            + (f" · region `{env_preview.region}`" if env_preview.region else "")
        )
        bdry1, bdry2, bdry3 = st.columns(3)
        with bdry1:
            if st.button("🧪 Dry run (simulate state diff)", use_container_width=True):
                new_flat = apply_command(state_flat, c)
                changed = {
                    k: (state_flat.get(k), v)
                    for k, v in new_flat.items()
                    if state_flat.get(k) != v
                }
                if changed:
                    st.success(f"Would mutate {len(changed)} state key(s):")
                    st.json({k: {"before": b, "after": a} for k, (b, a) in changed.items()})
                else:
                    st.warning("No state changes computed.")
        with bdry2:
            approve = st.button(
                "📝 Record as done (manual ack)",
                use_container_width=True,
                type="primary",
                help="Persists the hero-state diff (level / star_progress / skills) "
                "to `db/state.yaml`. Resource changes are skipped — the next "
                "scan_heroes_grid pass picks them up from the live screen.",
            )
        with bdry3:
            queue_btn = st.button(
                "🤖 Queue for bot",
                use_container_width=True,
                disabled=not instance_id,
                help="Pushes the generated scenario onto the worker queue "
                "(`wos:queue:<instance>`). The bot picks it up via the "
                "normal pop_due loop, navigates to the hero's screen, and "
                "executes the scenario's steps. Needs the scenario's "
                "`steps:` filled in (annotated buttons).",
            )
            if queue_btn:
                try:
                    client = get_redis()
                    qk = enqueue_envelope(env_preview, client)
                    st.success(
                        f"Queued task `{env_preview.task_id}` → `{qk}`. "
                        f"Scenario `{env_preview.dsl_scenario}` will run on "
                        f"instance `{instance_id}`."
                    )
                except Exception as exc:
                    st.error(f"Redis push failed: {type(exc).__name__}: {exc}")
            if approve:
                new_flat = apply_command(state_flat, c)
                full_diff = {
                    k: (state_flat.get(k), v)
                    for k, v in new_flat.items()
                    if state_flat.get(k) != v
                }
                # state_store._set_nested logs + skips unknown keys, so we
                # can hand the whole diff in. Resources accepts ``extra``
                # so ``resources.hero_xp`` / manuals / shards persist too.
                if full_diff:
                    persistable = {k: new_flat[k] for k in full_diff}
                    gamer_store = store.get_or_create(str(gamer.id), nickname=gamer.nickname)
                    gamer_store.update_from_flat(persistable)
                    st.success(
                        f"Recorded {len(persistable)} state key(s) → "
                        f"`db/state.yaml` + audit log."
                    )
                append_entry(
                    HistoryEntry(
                        approved_at=now_ts(),
                        gamer_id=str(gamer.id),
                        profile=ctx.active_profile_id,
                        candidate_id=c.id,
                        action=c.action,
                        hero_id=c.hero_id,
                        score=float(br.final_score),
                        costs=[
                            {"resource": x.resource, "amount": int(x.amount)}
                            for x in c.costs
                        ],
                        state_diff={
                            k: {"before": before, "after": after}
                            for k, (before, after) in full_diff.items()
                        },
                        reasons=reasons,
                        notes=list(br.notes),
                    )
                )
                st.rerun()


# ---------- Candidates ------------------------------------------------------
with tab_cand:
    st.caption(
        "All candidates the optimizer considered for this single solve. "
        "`status=selected` made the CP-SAT cut; `rejected` was kept but lost on "
        "score/budget; `pruned` was dropped by a hard rule before the solver."
    )
    cand_rows: list[dict[str, Any]] = []
    # Kept candidates (selected vs rejected by solver)
    for c in prune.kept:
        br = breakdowns.get(c.id)
        is_sel = c.id in selected_ids
        detail, from_repr, to_repr = _command_label(c)
        cand_rows.append(
            {
                "id": c.id,
                "hero": c.hero_id or "",
                "action": c.action,
                "detail": detail,
                "from": from_repr,
                "to": to_repr,
                "cost": _cost_str(c),
                "score": round(br.final_score, 1) if br else 0.0,
                "status": "selected" if is_sel else "rejected",
                "reasons": ", ".join(_reasons_for(c, br, is_selected=is_sel))
                if br
                else "",
                "drop_reason": "" if is_sel else rejection_reason(c, br),
            }
        )
    # Hard-rule prunes
    for c, reason in prune.dropped:
        detail, from_repr, to_repr = _command_label(c)
        cand_rows.append(
            {
                "id": c.id,
                "hero": c.hero_id or "",
                "action": c.action,
                "detail": detail,
                "from": from_repr,
                "to": to_repr,
                "cost": _cost_str(c),
                "score": 0.0,
                "status": "pruned",
                "reasons": "",
                "drop_reason": reason,
            }
        )
    if cand_rows:
        df = pd.DataFrame(cand_rows).sort_values(
            by=["status", "score"], ascending=[True, False]
        )
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("No candidates generated for this state.")


# ---------- Resources -------------------------------------------------------
with tab_res:
    st.caption(
        "Available = state inventory after the wheel-reserve floor. "
        "Spend = sum of selected commands' costs. Usage = spend / available."
    )
    spend_by_resource: dict[str, int] = {}
    for c in result.selected:
        for cost in c.costs:
            spend_by_resource[cost.resource] = (
                spend_by_resource.get(cost.resource, 0) + int(cost.amount)
            )

    res_rows: list[dict[str, Any]] = []
    all_resources = set(capacities) | set(spend_by_resource)
    for resource in sorted(all_resources):
        available = int(capacities.get(resource, 0))
        spend = int(spend_by_resource.get(resource, 0))
        remaining = max(0, available - spend)
        usage_pct = (spend / available) if available > 0 else 0.0
        if available == 0 and spend == 0:
            continue
        res_rows.append(
            {
                "resource": resource,
                "available": available,
                "selected_spend": spend,
                "remaining": remaining,
                "usage_pct": min(1.0, usage_pct),
            }
        )
    if res_rows:
        st.dataframe(
            pd.DataFrame(res_rows),
            width="stretch",
            hide_index=True,
            column_config={
                "usage_pct": st.column_config.ProgressColumn(
                    "Usage",
                    min_value=0,
                    max_value=1,
                    format="%.0f%%",
                )
            },
        )
    else:
        st.info(
            "No tracked resources have non-zero inventory or planned spend. "
            "Add inventory keys (e.g. `resources.hero_xp`) to `db/state.yaml` "
            "or wait for an OCR pass to populate them."
        )

    rsv = capacities.get("__reserve_gems_floor")  # placeholder; we don't surface yet
    # We DO have the floor from defaults but compute_capacities subtracts it
    # before we see it. Surface it as a caption when relevant.
    wheel_policy = ctx.active_profile.get("wheel_policy", "")
    if wheel_policy == "reserve_for_next_gen":
        st.caption(
            "Gems reserve floor (13 500) subtracted from `resources.diamond` "
            "before the solver sees it — change in `Config → Balance → Profiles`."
        )


# ---------- Score Breakdown -------------------------------------------------
with tab_score:
    st.caption(
        "Pick a candidate to see how its `final_score` is composed. The bar "
        "chart shows positive contributions (mode value × profile weight) "
        "minus penalties."
    )
    if not breakdowns:
        st.info("No scored candidates — nothing kept after the prune pass.")
    else:
        opt_ids = sorted(
            breakdowns.keys(),
            key=lambda cid: breakdowns[cid].final_score,
            reverse=True,
        )
        # Build a label that includes the hero + action for picking.
        labels = {}
        for c in prune.kept:
            br = breakdowns.get(c.id)
            if br is None:
                continue
            labels[c.id] = f"{c.action} · {c.hero_id or '–'} · score {br.final_score:.0f}"
        chosen_id = st.selectbox(
            "Candidate",
            options=opt_ids,
            format_func=lambda cid: labels.get(cid, cid),
            key="optimizer_score_pick",
        )
        br = breakdowns[chosen_id]
        cand = next(c for c in prune.kept if c.id == chosen_id)

        bd_rows = []
        for mode, val in br.mode_contributions.items():
            bd_rows.append({"component": f"mode/{mode}", "value": round(val, 2)})
        if br.threshold_bonus:
            bd_rows.append({"component": "threshold_bonus", "value": round(br.threshold_bonus, 2)})
        if br.replacement_penalty:
            bd_rows.append(
                {"component": "−replacement_penalty", "value": -round(br.replacement_penalty, 2)}
            )
        if br.resource_rarity_penalty:
            bd_rows.append(
                {"component": "−resource_penalty", "value": -round(br.resource_rarity_penalty, 2)}
            )
        bd_rows.append({"component": "final", "value": round(br.final_score, 2)})
        bd_df = pd.DataFrame(bd_rows)
        st.bar_chart(bd_df.set_index("component"))
        st.dataframe(bd_df, width="stretch", hide_index=True)

        with st.expander("Reasons", expanded=False):
            for r in _reasons_for(cand, br, is_selected=chosen_id in selected_ids):
                st.markdown(f"- `{r}`")
        if cand.hero_id:
            with st.expander("Hero meta", expanded=False):
                st.json(ctx.hero_meta(cand.hero_id))


# ---------- Constraints -----------------------------------------------------
with tab_constr:
    st.caption(
        "Hard rules drop candidates *before* the solver sees them — they're "
        "never penalties, just plain ineligibility. Pipeline graph below "
        "shows where each stage fits."
    )

    st.subheader("Active rule set (from `config/balance/`)")
    profile = ctx.active_profile
    rule_rows = [
        {
            "rule": "general_shard_policy.mythic",
            "source": "profiles.yaml",
            "active_value": (profile.get("general_shard_policy") or {}).get("mythic"),
        },
        {
            "rule": "general_shard_policy.epic",
            "source": "profiles.yaml",
            "active_value": (profile.get("general_shard_policy") or {}).get("epic"),
        },
        {
            "rule": "wheel_policy + gems_reserve_floor",
            "source": "profiles.yaml + capacities.py",
            "active_value": profile.get("wheel_policy"),
        },
        {
            "rule": "stop_replacement (Sergey ← Flint, …)",
            "source": "hard_rules.py:_STOP_REPLACEMENTS",
            "active_value": "Flint unlocked → block sergey star_tier_up / gear_*",
        },
        {
            "rule": "support_level_cap (joiner manual XP)",
            "source": "hero_meta.yaml.manual_level_cap_*",
            "active_value": "joiner tags × drill_camp gate",
        },
        {
            "rule": "skill_cap_by_star",
            "source": "cost_tables.yaml.skill_level_cap_by_star_v1",
            "active_value": ctx.cost_tables.get("skill_level_cap_by_star_v1"),
        },
    ]
    st.dataframe(pd.DataFrame(rule_rows), width="stretch", hide_index=True)

    if prune.dropped:
        st.subheader(f"Pruned this solve ({len(prune.dropped)})")
        reasons_grouped: dict[str, list[str]] = {}
        for c, reason in prune.dropped:
            reasons_grouped.setdefault(reason, []).append(c.id)
        for reason, ids in sorted(reasons_grouped.items()):
            with st.expander(f"`{reason}` · {len(ids)} candidate(s)", expanded=False):
                for cid in ids:
                    st.markdown(f"- `{cid}`")
    else:
        st.success("No candidates pruned by hard rules in this solve.")

    st.subheader("Pipeline")
    st.graphviz_chart(
        """
        digraph optimizer_pipeline {
            rankdir=LR;
            node [shape=box, style=rounded, fontname="Helvetica"];
            "db/state.yaml"          -> "candidate_generator";
            "config/balance/*.yaml"  -> "candidate_generator";
            "candidate_generator"    -> "hard_rules (prune)";
            "hard_rules (prune)"     -> "scorer";
            "scorer"                 -> "CP-SAT solver";
            "compute_capacities"     -> "CP-SAT solver";
            "CP-SAT solver"          -> "plan_top_k (re-optimize loop)";
            "plan_top_k (re-optimize loop)" -> "Next Command UI";
            "Next Command UI"        -> "apply_command (manual ack)";
            "apply_command (manual ack)" -> "db/state.yaml";
        }
        """
    )


# ---------- History ---------------------------------------------------------
with tab_history:
    st.caption(
        "Audit log of operator-approved commands — `db/optimizer_history.yaml`. "
        "Each entry captures candidate, score, costs, persisted state diff."
    )
    history = load_history()
    if not history:
        st.info("History is empty. Approve commands in `Next Command` to populate.")
    else:
        # Most recent first; filter by current gamer.
        recent = list(reversed(history))
        current_only = st.checkbox(
            f"Only this gamer (`{gamer.id}`)",
            value=True,
            key="optimizer_history_filter",
        )
        if current_only:
            recent = [h for h in recent if h.gamer_id == str(gamer.id)]
        st.caption(f"Showing {len(recent)} entry(ies).")
        hist_rows = [
            {
                "approved_at": (
                    pd.Timestamp(h.approved_at, unit="s")
                    .tz_localize("UTC")
                    .tz_convert("Europe/Berlin")
                    .strftime("%Y-%m-%d %H:%M:%S")
                    if h.approved_at
                    else ""
                ),
                "gamer": h.gamer_id,
                "profile": h.profile,
                "candidate": h.candidate_id,
                "action": h.action,
                "hero": h.hero_id or "",
                "score": round(h.score, 1),
                "cost": ", ".join(f"{c['amount']} {c['resource']}" for c in h.costs) or "—",
                "diff_keys": len(h.state_diff),
            }
            for h in recent
        ]
        if hist_rows:
            st.dataframe(pd.DataFrame(hist_rows), width="stretch", hide_index=True)
            with st.expander("Inspect a record", expanded=False):
                ids = [f"{r['approved_at']} · {r['candidate']}" for r in hist_rows]
                if ids:
                    sel = st.selectbox(
                        "Entry",
                        options=range(len(ids)),
                        format_func=lambda i: ids[i],
                        key="optimizer_history_pick",
                    )
                    e = recent[sel]
                    st.json(
                        {
                            "approved_at": e.approved_at,
                            "gamer_id": e.gamer_id,
                            "profile": e.profile,
                            "candidate_id": e.candidate_id,
                            "action": e.action,
                            "hero_id": e.hero_id,
                            "score": e.score,
                            "costs": e.costs,
                            "state_diff": e.state_diff,
                            "reasons": e.reasons,
                            "notes": e.notes,
                        }
                    )


# ---------- Raw -------------------------------------------------------------
with tab_raw:
    st.caption(
        "Full pipeline payloads for debugging — paste into a bug report or "
        "diff against a baseline. Resource keys at the top reflect the "
        "compute_capacities pass."
    )
    raw_payload = {
        "solver": {
            "status": result.status,
            "objective": result.objective_value,
            "selected": list(result.chosen_ids),
        },
        "active_profile": ctx.active_profile_id,
        "profile_config": ctx.active_profile,
        "capacities_after_reserve": capacities,
        "candidates_kept": [
            {
                "id": c.id,
                "action": c.action,
                "hero": c.hero_id,
                "priority_band": c.priority_band,
                "costs": [
                    {"resource": cost.resource, "amount": cost.amount} for cost in c.costs
                ],
                "payload": c.payload,
                "score": {
                    "final": round(breakdowns[c.id].final_score, 3),
                    "base": round(breakdowns[c.id].base_value, 3),
                    "upgrade_gain": round(breakdowns[c.id].upgrade_gain, 3),
                    "threshold_bonus": round(breakdowns[c.id].threshold_bonus, 3),
                    "modes": {
                        m: round(v, 2)
                        for m, v in breakdowns[c.id].mode_contributions.items()
                    },
                    "replacement_penalty": round(breakdowns[c.id].replacement_penalty, 3),
                    "resource_penalty": round(breakdowns[c.id].resource_rarity_penalty, 3),
                    "notes": list(breakdowns[c.id].notes),
                },
                "reasons": _reasons_for(
                    c, breakdowns[c.id], is_selected=c.id in selected_ids
                ),
            }
            for c in prune.kept
        ],
        "candidates_pruned": [
            {"id": c.id, "reason": reason} for c, reason in prune.dropped
        ],
        "plan_top_k": [
            {
                "step": i,
                "id": step.candidate.id,
                "action": step.candidate.action,
                "hero": step.candidate.hero_id,
                "score": round(step.breakdown.final_score, 1),
                "cost": [
                    {"resource": x.resource, "amount": x.amount}
                    for x in step.candidate.costs
                ],
                "capacities_before": step.capacities_before,
                "capacities_after": step.capacities_after,
            }
            for i, step in enumerate(plan, start=1)
        ],
    }

    st.subheader("solver_result (JSON)")
    st.json(raw_payload)

    with st.expander("Flat state used for this solve", expanded=False):
        st.json(state_flat)

    import yaml as _yaml

    st.download_button(
        "⬇️ Download as YAML",
        data=_yaml.safe_dump(raw_payload, sort_keys=False, allow_unicode=True),
        file_name="solver_result.yaml",
        mime="application/x-yaml",
    )
