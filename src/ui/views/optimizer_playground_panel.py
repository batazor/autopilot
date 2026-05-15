"""Playground tab — synthetic state, read-only solve (nothing persisted)."""
from __future__ import annotations

import dataclasses
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from config.heroes import get_hero_registry
from optimizer import (
    compute_capacities,
    load_balance_context,
    plan_top_k,
    rejection_reason,
    solve_optimal,
)
from optimizer.context import BalanceContext
from ui.views.optimizer_ui import (
    candidate_table_rows,
    command_label,
    cost_str,
    reasons_for,
    render_solver_metrics,
)


def _heroes_index() -> list[tuple[str, str]]:
    out = [(h.id, h.name) for h in get_hero_registry().heroes if h.id]
    out.sort(key=lambda x: x[1].lower())
    return out


def _hero_form(hid: str, name: str) -> dict[str, Any]:
    out: dict[str, Any] = {f"heroes.entries.{hid}.available": True}
    with st.expander(f"{name} · `{hid}`", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            level = st.number_input(
                "level",
                min_value=1,
                max_value=80,
                value=int(st.session_state.get(f"pg_lv_{hid}", 5)),
                step=1,
                key=f"pg_lv_{hid}",
            )
            out[f"heroes.entries.{hid}.level"] = int(level)
        with c2:
            star_progress = st.number_input(
                "star_progress (0..30)",
                min_value=0,
                max_value=30,
                value=int(st.session_state.get(f"pg_star_{hid}", 0)),
                step=1,
                key=f"pg_star_{hid}",
            )
            out[f"heroes.entries.{hid}.star_progress"] = int(star_progress)
        with c3:
            shards = st.number_input(
                "shards_current",
                min_value=0,
                max_value=10_000,
                value=int(st.session_state.get(f"pg_shards_{hid}", 0)),
                step=1,
                key=f"pg_shards_{hid}",
            )
            out[f"heroes.entries.{hid}.shards_current"] = int(shards)
        st.markdown("**Skill levels** (0 = unlearned)")
        sc1, sc2 = st.columns(2)
        with sc1:
            for slot in (1, 2):
                key = f"pg_skill_exp_{hid}_{slot}"
                v = st.number_input(
                    f"expedition.{slot}",
                    min_value=0,
                    max_value=5,
                    value=int(st.session_state.get(key, 0)),
                    step=1,
                    key=key,
                )
                out[f"heroes.entries.{hid}.skills.expedition.{slot}"] = int(v)
        with sc2:
            for slot in (1, 2):
                key = f"pg_skill_expl_{hid}_{slot}"
                v = st.number_input(
                    f"exploration.{slot}",
                    min_value=0,
                    max_value=5,
                    value=int(st.session_state.get(key, 0)),
                    step=1,
                    key=key,
                )
                out[f"heroes.entries.{hid}.skills.exploration.{slot}"] = int(v)
    return out


def render_playground_panel() -> None:
    st.caption(
        "What-if sandbox: build synthetic state, see what the optimizer would pick. "
        "**Nothing is persisted** — no `db/state.yaml`, Redis, or audit log."
    )

    heroes = _heroes_index()
    if not heroes:
        st.warning(
            "No heroes in the wiki registry. Check "
            "`modules/core/heroes/wiki/heroes/index.yaml`."
        )
        st.stop()
    ctx_base = load_balance_context()
    profile_options = list(ctx_base.profiles.keys())
    default_idx = (
        profile_options.index(ctx_base.active_profile_id)
        if ctx_base.active_profile_id in profile_options
        else 0
    )

    cfg_col, roster_col = st.columns([1, 1.6], gap="large")

    with cfg_col:
        st.markdown("**Context & budgets**")
        profile_id = st.selectbox(
            "Profile",
            options=profile_options or [ctx_base.active_profile_id or "(none)"],
            index=default_idx if profile_options else 0,
            key="playground_profile",
        )
        ctx: BalanceContext = (
            dataclasses.replace(ctx_base, active_profile_id=profile_id)
            if profile_id != ctx_base.active_profile_id
            else ctx_base
        )
        server_age = int(
            st.slider(
                "server_age_days",
                min_value=0,
                max_value=180,
                value=30,
                key="playground_age",
            )
        )
        furnace_level = int(
            st.slider(
                "chief.furnace_level",
                min_value=10,
                max_value=80,
                value=25,
                key="playground_furnace",
            )
        )
        drill_camp = st.checkbox(
            "drill_camp_unlocked",
            value=furnace_level >= 13,
            key="playground_drill",
        )
        plan_k = int(
            st.slider("Plan K", min_value=1, max_value=15, value=8, key="playground_plan_k")
        )
        hero_xp = int(
            st.number_input(
                "hero_xp",
                min_value=0,
                max_value=10_000_000,
                value=40_000,
                step=1_000,
                key="playground_hero_xp",
            )
        )
        gems = int(
            st.number_input(
                "diamond (gems)",
                min_value=0,
                max_value=10_000_000,
                value=30_000,
                step=1_000,
                key="playground_gems",
            )
        )
        manual_inputs: dict[str, int] = {}
        for rarity in ("rare", "epic", "mythic"):
            for track in ("expedition", "exploration"):
                key = f"{rarity}_{track}_manual"
                manual_inputs[key] = int(
                    st.number_input(
                        key,
                        min_value=0,
                        max_value=10_000,
                        value=20 if rarity == "epic" else 0,
                        step=1,
                        key=f"playground_manual_{key}",
                    )
                )

    with roster_col:
        st.markdown("**Hero roster**")
        default_available = ["molly", "bahiti", "sergey", "jeronimo", "jessie", "jasser"]
        available_ids = st.multiselect(
            "Available heroes",
            options=[hid for hid, _ in heroes],
            default=[hid for hid in default_available if any(h == hid for h, _ in heroes)],
            format_func=lambda hid: next((n for h, n in heroes if h == hid), hid),
            key="playground_available",
        )
        hero_overrides: dict[str, Any] = {}
        for hid in available_ids:
            name = next((n for h, n in heroes if h == hid), hid)
            hero_overrides.update(_hero_form(hid, name))

    state_flat: dict[str, Any] = {
        "chief.furnace_level": furnace_level,
        "account.drill_camp_unlocked": bool(drill_camp),
        "resources.hero_xp": hero_xp,
        "resources.diamond": gems,
    }
    for k, v in manual_inputs.items():
        state_flat[f"resources.{k}"] = v
    state_flat.update(hero_overrides)

    result, prune, breakdowns = solve_optimal(state_flat, ctx, server_age_days=server_age)
    plan = plan_top_k(state_flat, ctx, k=plan_k, server_age_days=server_age)
    capacities = compute_capacities(state_flat, ctx)

    selected_ids = set(result.chosen_ids)
    selected_count = len(result.selected)
    kept_count = len(prune.kept)
    pruned_count = len(prune.dropped)
    rejected_count = kept_count - selected_count
    profile_desc = str(ctx.active_profile.get("description") or "").strip()

    render_solver_metrics(
        status=result.status,
        objective=result.objective_value,
        selected_count=selected_count,
        rejected_count=rejected_count,
        pruned_count=pruned_count,
        profile_id=profile_id,
        profile_description=profile_desc,
    )

    tab_plan, tab_candidates, tab_resources, tab_score, tab_state = st.tabs(
        ["Plan", "Candidates", "Resources", "Score", "Synth state"]
    )

    with tab_plan:
        if not plan:
            st.info("Planner returned no steps — bump budgets or add heroes.")
        else:
            rows: list[dict[str, Any]] = []
            for i, step in enumerate(plan, start=1):
                c = step.candidate
                br = step.breakdown
                detail, from_repr, to_repr = command_label(c)
                rows.append(
                    {
                        "#": i,
                        "command": c.action,
                        "hero": c.hero_id or "",
                        "detail": detail,
                        "from": from_repr,
                        "to": to_repr,
                        "cost": cost_str(c),
                        "score": round(br.final_score, 1),
                        "reasons": ", ".join(reasons_for(c, br, ctx, is_selected=True)),
                    }
                )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            if len(plan) > 1:
                st.divider()
                st.markdown("**Resource diff after full plan**")
                first = plan[0].capacities_before
                last = plan[-1].capacities_after
                diff_rows = []
                for resource in sorted(set(first) | set(last)):
                    before = first.get(resource, 0)
                    after = last.get(resource, 0)
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

    with tab_candidates:
        cand_rows = candidate_table_rows(
            prune_kept=prune.kept,
            prune_dropped=prune.dropped,
            breakdowns=breakdowns,
            selected_ids=selected_ids,
            ctx=ctx,
            rejection_reason_fn=rejection_reason,
        )
        if cand_rows:
            df = pd.DataFrame(cand_rows).sort_values(
                by=["status", "score"], ascending=[True, False]
            )
            st.dataframe(df, width="stretch", hide_index=True)
        else:
            st.info("No candidates — pick some heroes.")

    with tab_resources:
        spend_by_resource: dict[str, int] = {}
        for c in result.selected:
            for cost in c.costs:
                spend_by_resource[cost.resource] = (
                    spend_by_resource.get(cost.resource, 0) + int(cost.amount)
                )
        res_rows: list[dict[str, Any]] = []
        for resource in sorted(set(capacities) | set(spend_by_resource)):
            available = int(capacities.get(resource, 0))
            spend = int(spend_by_resource.get(resource, 0))
            if available == 0 and spend == 0:
                continue
            remaining = max(0, available - spend)
            usage = (spend / available) if available > 0 else 0.0
            res_rows.append(
                {
                    "resource": resource,
                    "available": available,
                    "selected_spend": spend,
                    "remaining": remaining,
                    "usage_pct": min(1.0, usage),
                }
            )
        if res_rows:
            st.dataframe(
                pd.DataFrame(res_rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "usage_pct": st.column_config.ProgressColumn(
                        "Usage", min_value=0, max_value=1, format="%.0f%%"
                    )
                },
            )
        else:
            st.info("All resources at 0.")

    with tab_score:
        if not breakdowns:
            st.info("Nothing scored — add heroes / budgets.")
        else:
            labels = {
                c.id: f"{c.action} · {c.hero_id or '–'} · score {breakdowns[c.id].final_score:.0f}"
                for c in prune.kept
                if breakdowns.get(c.id) is not None
            }
            opt_ids = sorted(labels, key=lambda cid: breakdowns[cid].final_score, reverse=True)
            sel = st.selectbox(
                "Candidate",
                options=opt_ids,
                format_func=lambda cid: labels.get(cid, cid),
                key="playground_score_pick",
            )
            br = breakdowns[sel]
            bd_rows = [
                {"component": f"mode/{m}", "value": round(v, 2)}
                for m, v in br.mode_contributions.items()
            ]
            if br.threshold_bonus:
                bd_rows.append(
                    {"component": "threshold_bonus", "value": round(br.threshold_bonus, 2)}
                )
            if br.replacement_penalty:
                bd_rows.append(
                    {
                        "component": "−replacement_penalty",
                        "value": -round(br.replacement_penalty, 2),
                    }
                )
            if br.resource_rarity_penalty:
                bd_rows.append(
                    {
                        "component": "−resource_penalty",
                        "value": -round(br.resource_rarity_penalty, 2),
                    }
                )
            bd_rows.append({"component": "final", "value": round(br.final_score, 2)})
            bd_df = pd.DataFrame(bd_rows)
            st.bar_chart(bd_df.set_index("component"))

    with tab_state:
        st.caption("Flat state passed to the solver — copy or download as a test fixture.")
        st.json(state_flat)
        st.download_button(
            "Download synth state as YAML",
            data=yaml.safe_dump(state_flat, sort_keys=True, allow_unicode=True),
            file_name="playground_state.yaml",
            mime="application/x-yaml",
        )
