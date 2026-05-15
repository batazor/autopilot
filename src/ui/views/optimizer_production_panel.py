"""Production tab — live ``db/state.yaml``, approve / queue for bot."""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
import yaml

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
from ui.redis_client import get_redis
from ui.views.optimizer_ui import (
    candidate_table_rows,
    command_label,
    cost_str,
    reasons_for,
    render_solver_metrics,
)


def _dataframe_scalar(value: Any) -> str:
    """Render nested config values as strings so PyArrow can serialize the column."""
    if isinstance(value, dict | list | tuple):
        return yaml.safe_dump(value, sort_keys=True, allow_unicode=True).strip()
    if value is None:
        return ""
    return str(value)


def render_production_panel() -> None:
    st.caption(
        "Live state from `db/state.yaml`. Only step #1 of the plan is safe to execute — "
        "later steps assume earlier ones already ran."
    )

    store = get_state_store()
    db = store._db
    gamers = list(db.gamers)
    if not gamers:
        st.warning("`db/state.yaml` has no gamers yet. Add one on **Player state**.")
        st.stop()

    c_gamer, c_inst, c_age, c_plan = st.columns([2.2, 1.5, 1, 1], vertical_alignment="bottom")
    with c_gamer:
        gamer_idx = st.selectbox(
            "Gamer",
            options=range(len(gamers)),
            format_func=lambda i: f"{gamers[i].nickname or '(no nick)'} · `{gamers[i].id}`",
            key="optimizer_gamer",
        )
        if gamer_idx is None:
            st.stop()
        gamer = gamers[int(gamer_idx)]
    with c_inst:
        settings = load_settings()
        instance_ids = [inst.instance_id for inst in settings.instances]
        if instance_ids:
            instance_id = st.selectbox(
                "Instance (Queue for bot)",
                options=instance_ids,
                key="optimizer_instance",
            )
        else:
            st.caption("No instances configured.")
            instance_id = ""
    with c_age:
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
    with c_plan:
        plan_k = int(
            st.number_input(
                "Plan K",
                min_value=1,
                max_value=20,
                value=8,
                step=1,
                key="optimizer_plan_k",
            )
        )

    ctx = load_balance_context()
    state_flat = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()
    profile_desc = str(ctx.active_profile.get("description") or "").strip()
    st.caption(f"Active profile: `{ctx.active_profile_id or '(none)'}`" + (
        f" — {profile_desc}" if profile_desc else ""
    ))

    result, prune, breakdowns = solve_optimal(state_flat, ctx, server_age_days=server_age)
    plan = plan_top_k(state_flat, ctx, k=plan_k, server_age_days=server_age)
    capacities = compute_capacities(state_flat, ctx)

    selected_ids = set(result.chosen_ids)
    selected_count = len(result.selected)
    kept_count = len(prune.kept)
    pruned_count = len(prune.dropped)
    rejected_count = kept_count - selected_count

    render_solver_metrics(
        status=result.status,
        objective=result.objective_value,
        selected_count=selected_count,
        rejected_count=rejected_count,
        pruned_count=pruned_count,
        profile_id=ctx.active_profile_id or "",
        profile_description=profile_desc,
    )

    _render_next_command(
        store=store,
        gamer=gamer,
        instance_id=instance_id,
        state_flat=state_flat,
        ctx=ctx,
        plan=plan,
    )

    tab_plan, tab_candidates, tab_resources, tab_score, tab_audit, tab_raw = st.tabs(
        ["Plan", "Candidates", "Resources", "Score", "Audit", "Raw"]
    )

    with tab_plan:
        st.caption(
            f"Top-{plan_k} preview of solve → apply top-1 → re-solve. "
            "Step #1 matches **Next command** above."
        )
        if not plan:
            st.info("Planner returned no steps — capacities are probably 0.")
        else:
            queue_rows: list[dict[str, Any]] = []
            for i, step in enumerate(plan, start=1):
                c = step.candidate
                br = step.breakdown
                detail, from_repr, to_repr = command_label(c)
                queue_rows.append(
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
                        "status": "next" if i == 1 else "preview",
                    }
                )
            st.dataframe(pd.DataFrame(queue_rows), width="stretch", hide_index=True)
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
        st.caption(
            "Single-solve view: `selected` / `rejected` / `pruned`."
        )
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
            st.info("No candidates generated for this state.")

    with tab_resources:
        st.caption("Available after wheel-reserve; spend = selected commands.")
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
                "No tracked resources with inventory or planned spend. "
                "Populate `resources.*` in `db/state.yaml` or wait for OCR."
            )
        if ctx.active_profile.get("wheel_policy") == "reserve_for_next_gen":
            st.caption(
                "Gems reserve floor subtracted from `resources.diamond` — "
                "change in **Balance → Profiles**."
            )

    with tab_score:
        st.caption("How `final_score` composes for one candidate.")
        if not breakdowns:
            st.info("No scored candidates — nothing kept after the prune pass.")
        else:
            labels: dict[str, str] = {}
            for c in prune.kept:
                br = breakdowns.get(c.id)
                if br is None:
                    continue
                labels[c.id] = f"{c.action} · {c.hero_id or '–'} · score {br.final_score:.0f}"
            opt_ids = sorted(
                labels,
                key=lambda cid: breakdowns[cid].final_score,
                reverse=True,
            )
            chosen_id = st.selectbox(
                "Candidate",
                options=opt_ids,
                format_func=lambda cid: labels.get(cid, cid),
                key="optimizer_score_pick",
            )
            br = breakdowns[chosen_id]
            cand = next(c for c in prune.kept if c.id == chosen_id)
            bd_rows: list[dict[str, Any]] = []
            for mode, val in br.mode_contributions.items():
                bd_rows.append({"component": f"mode/{mode}", "value": round(val, 2)})
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
            st.dataframe(bd_df, width="stretch", hide_index=True)
            with st.expander("Reasons", expanded=False):
                for r in reasons_for(
                    cand, br, ctx, is_selected=chosen_id in selected_ids
                ):
                    st.markdown(f"- `{r}`")
            if cand.hero_id:
                with st.expander("Hero meta", expanded=False):
                    st.json(ctx.hero_meta(cand.hero_id))

    with tab_audit:
        audit_rules, audit_history = st.tabs(["Hard rules", "History"])
        with audit_rules:
            st.caption("Hard rules drop candidates before CP-SAT.")
            profile = ctx.active_profile
            rule_rows = [
                {
                    "rule": "general_shard_policy.mythic",
                    "source": "profiles.yaml",
                    "active_value": _dataframe_scalar((profile.get("general_shard_policy") or {}).get("mythic")),
                },
                {
                    "rule": "general_shard_policy.epic",
                    "source": "profiles.yaml",
                    "active_value": _dataframe_scalar((profile.get("general_shard_policy") or {}).get("epic")),
                },
                {
                    "rule": "wheel_policy + gems_reserve_floor",
                    "source": "profiles.yaml + capacities.py",
                    "active_value": _dataframe_scalar(profile.get("wheel_policy")),
                },
                {
                    "rule": "stop_replacement",
                    "source": "hard_rules.py",
                    "active_value": _dataframe_scalar("Flint unlocked → block sergey star_tier_up / gear_*"),
                },
                {
                    "rule": "support_level_cap",
                    "source": "hero_meta.yaml",
                    "active_value": _dataframe_scalar("joiner tags × drill_camp gate"),
                },
                {
                    "rule": "skill_cap_by_star",
                    "source": "cost_tables.yaml",
                    "active_value": _dataframe_scalar(ctx.cost_tables.get("skill_level_cap_by_star_v1")),
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
            with st.expander("Pipeline", expanded=False):
                st.graphviz_chart(
                    """

                    digraph optimizer_pipeline {
                        rankdir=LR;
                        node [shape=box, style=rounded, fontname="Helvetica"];
                        "db/state.yaml" -> "candidate_generator";
                        "config/balance/*.yaml" -> "candidate_generator";
                        "candidate_generator" -> "hard_rules";
                        "hard_rules" -> "scorer";
                        "scorer" -> "CP-SAT";
                        "CP-SAT" -> "plan_top_k";
                        "plan_top_k" -> "UI";
                        "UI" -> "apply_command";
                        "apply_command" -> "db/state.yaml";
                    }
                    """
                )
        with audit_history:
            st.caption("Operator-approved commands — `db/optimizer_history.yaml`.")
            history = load_history()
            if not history:
                st.info("History is empty. Use **Record as done** on the next command card.")
            else:
                recent = list(reversed(history))
                current_only = st.checkbox(
                    f"Only gamer `{gamer.id}`",
                    value=True,
                    key="optimizer_history_filter",
                )
                if current_only:
                    recent = [h for h in recent if h.gamer_id == str(gamer.id)]
                st.caption(f"{len(recent)} entry(ies).")
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
                        "cost": ", ".join(f"{c['amount']} {c['resource']}" for c in h.costs)
                        or "—",
                        "diff_keys": len(h.state_diff),
                    }
                    for h in recent
                ]
                if hist_rows:
                    st.dataframe(pd.DataFrame(hist_rows), width="stretch", hide_index=True)
                    with st.expander("Inspect entry", expanded=False):
                        ids = [f"{r['approved_at']} · {r['candidate']}" for r in hist_rows]
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

    with tab_raw:
        st.caption("Full pipeline payloads for bug reports or regression diffs.")
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
                        "replacement_penalty": round(
                            breakdowns[c.id].replacement_penalty, 3
                        ),
                        "resource_penalty": round(
                            breakdowns[c.id].resource_rarity_penalty, 3
                        ),
                        "notes": list(breakdowns[c.id].notes),
                    },
                    "reasons": reasons_for(
                        c, breakdowns[c.id], ctx, is_selected=c.id in selected_ids
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
        st.json(raw_payload)
        with st.expander("Flat state used for this solve", expanded=False):
            st.json(state_flat)
        st.download_button(
            "Download solver_result.yaml",
            data=yaml.safe_dump(raw_payload, sort_keys=False, allow_unicode=True),
            file_name="solver_result.yaml",
            mime="application/x-yaml",
        )


def _render_next_command(
    *,
    store: Any,
    gamer: Any,
    instance_id: str,
    state_flat: dict[str, Any],
    ctx: Any,
    plan: list[Any],
) -> None:
    with st.container(border=True):
        st.subheader("Next command")
        if not plan:
            st.warning("No executable next command — solver found nothing for this state.")
            return

        step = plan[0]
        c = step.candidate
        br = step.breakdown
        detail, from_repr, to_repr = command_label(c)
        reasons = reasons_for(c, br, ctx, is_selected=True)

        headline = f"**{c.action}** · `{c.hero_id or '—'}`"
        if detail:
            headline += f" · {detail}"
        if from_repr:
            headline += f" · {from_repr} → {to_repr}"
        st.markdown(headline)

        meta = st.columns(4)
        meta[0].metric("Score", f"{br.final_score:.1f}")
        meta[1].metric("Cost", cost_str(c) or "—")
        meta[2].metric("Band", c.priority_band)
        meta[3].metric("Plan steps", len(plan))

        left, right = st.columns(2)
        with left:
            st.markdown(
                f"Base `{br.base_value:.1f}` · gain ×`{br.upgrade_gain:.2f}`"
                + (f" · threshold +`{br.threshold_bonus:.0f}`" if br.threshold_bonus else "")
                + f" · penalties `{br.replacement_penalty + br.resource_rarity_penalty:.1f}`"
            )
            st.markdown("**Why**")
            for r in reasons:
                st.markdown(f"- `{r}`")
            if c.preconditions:
                with st.expander("Preconditions", expanded=False):
                    for p in c.preconditions:
                        st.markdown(f"- {p}")
        with right:
            st.markdown("**Resource diff after this step**")
            diff_rows: list[dict[str, Any]] = []
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

        env_preview = build_envelope(
            c, player_id=str(gamer.id), instance_id=instance_id or "(default)"
        )
        st.caption(
            f"Dispatch: scenario `{env_preview.dsl_scenario}` · node `{env_preview.set_node}`"
            + (f" · region `{env_preview.region}`" if env_preview.region else "")
        )

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("Dry run (state diff)", width="stretch", key="optimizer_dry_run"):
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
        with b2:
            approve = st.button(
                "Record as done",
                width="stretch",
                type="primary",
                key="optimizer_record_done",
                help="Persists hero-state diff to `db/state.yaml` + audit log.",
            )
        with b3:
            queue_btn = st.button(
                "Queue for bot",
                width="stretch",
                disabled=not instance_id,
                key="optimizer_queue_bot",
                help="Pushes the scenario onto `wos:queue:<instance>`.",
            )
            if queue_btn:
                try:
                    client = get_redis()
                    qk = enqueue_envelope(env_preview, client)
                    st.success(
                        f"Queued `{env_preview.task_id}` → `{qk}` "
                        f"(`{env_preview.dsl_scenario}` on `{instance_id}`)."
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
            if full_diff:
                persistable = {k: new_flat[k] for k in full_diff}
                gamer_store = store.get_or_create(str(gamer.id), nickname=gamer.nickname)
                gamer_store.update_from_flat(persistable)
                st.success(
                    f"Recorded {len(persistable)} state key(s) → `db/state.yaml` + audit log."
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
                        {"resource": x.resource, "amount": int(x.amount)} for x in c.costs
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
