"""What-if sandbox for the upgrade optimizer.

Builds a synthetic flat-state from a form on the left (heroes / levels /
star progress / skills / shards / resources / furnace / server age),
optionally overrides the active profile, then re-runs the full
``solve_optimal`` + ``plan_top_k`` pipeline on that state. **Nothing is
written back** — neither ``db/state.yaml`` nor Redis nor the audit log —
so you can rip through balance experiments without polluting real data.

Use cases:

* "What does the conservative profile say if I had 100k more hero_xp?"
* "How does ranking change between conservative vs. bear_alliance_support
  at server day 45?"
* "Reproduce a regression: synth a state, see what the solver picks,
  paste it into a test as a fixture."

Distinct from the production ``Optimizer`` page in two ways: the Approve
buttons are gone (read-only) and the state lives in ``st.session_state``
instead of ``db/state.yaml``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pandas as pd
import streamlit as st

from optimizer import (
    compute_capacities,
    generate_reasons,
    load_balance_context,
    plan_top_k,
    rejection_reason,
    solve_optimal,
)
from optimizer.context import invalidate_balance_context
from optimizer.scorer import ScoreBreakdown
from optimizer.types import Candidate

st.title("Optimizer · playground")
st.caption(
    "What-if sandbox. Build a synthetic state on the left, see what the "
    "optimizer would do for it. **Nothing is persisted** — pure exploration."
)


# -------------------------------------------------------------------------
# Heroes index (for selecting which heroes are available in the synth state)
# -------------------------------------------------------------------------

def _heroes_index() -> list[tuple[str, str]]:
    """Sorted ``[(hero_id, display_name)]`` from ``db/heroes/index.yaml``."""
    from pathlib import Path

    import yaml

    repo = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo / "db" / "heroes" / "index.yaml").read_text(encoding="utf-8")) or {}
    out: list[tuple[str, str]] = []
    for entry in raw.get("heroes", []) or []:
        if not isinstance(entry, dict):
            continue
        hid = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip() or hid
        if hid:
            out.append((hid, name))
    out.sort(key=lambda x: x[1].lower())
    return out


_HEROES = _heroes_index()
_HERO_ID_BY_NAME = {f"{name} · {hid}": hid for hid, name in _HEROES}


# -------------------------------------------------------------------------
# Profile override + global tunables (sidebar)
# -------------------------------------------------------------------------

st.sidebar.subheader("Context")

if st.sidebar.button("🔄 Reload balance configs", use_container_width=True):
    invalidate_balance_context()
    st.success("Balance cache cleared.")

_ctx_base = load_balance_context()
profile_options = list(_ctx_base.profiles.keys())
default_idx = (
    profile_options.index(_ctx_base.active_profile_id)
    if _ctx_base.active_profile_id in profile_options
    else 0
)
profile_id = st.sidebar.selectbox(
    "Profile",
    options=profile_options or [_ctx_base.active_profile_id or "(none)"],
    index=default_idx if profile_options else 0,
    key="playground_profile",
)
ctx = (
    dataclasses.replace(_ctx_base, active_profile_id=profile_id)
    if profile_id != _ctx_base.active_profile_id
    else _ctx_base
)

server_age = int(
    st.sidebar.slider(
        "server_age_days",
        min_value=0,
        max_value=180,
        value=30,
        step=1,
        key="playground_age",
    )
)
furnace_level = int(
    st.sidebar.slider(
        "chief.furnace_level",
        min_value=10,
        max_value=80,
        value=25,
        step=1,
        key="playground_furnace",
    )
)
drill_camp = st.sidebar.checkbox(
    "drill_camp_unlocked (Furnace 13+)",
    value=furnace_level >= 13,
    key="playground_drill",
)
plan_k = int(
    st.sidebar.slider(
        "Plan K",
        min_value=1,
        max_value=15,
        value=8,
        step=1,
        key="playground_plan_k",
    )
)


# -------------------------------------------------------------------------
# Resource budgets (sidebar)
# -------------------------------------------------------------------------

st.sidebar.subheader("Resources")
hero_xp = int(
    st.sidebar.number_input(
        "hero_xp",
        min_value=0,
        max_value=10_000_000,
        value=40_000,
        step=1_000,
        key="playground_hero_xp",
    )
)
gems = int(
    st.sidebar.number_input(
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
            st.sidebar.number_input(
                key,
                min_value=0,
                max_value=10_000,
                value=20 if rarity == "epic" else 0,
                step=1,
                key=f"playground_manual_{key}",
            )
        )


# -------------------------------------------------------------------------
# Hero roster (main area)
# -------------------------------------------------------------------------

st.subheader("Heroes")
st.caption(
    "Pick which heroes are available + set their per-card state. Each "
    "selected hero contributes ``level_up`` / ``star_tier_up`` / ``skill_up`` "
    "candidates the optimizer will weigh."
)

default_available = ["molly", "bahiti", "sergey", "jeronimo", "jessie", "jasser"]
available_ids = st.multiselect(
    "Available heroes",
    options=[hid for hid, _ in _HEROES],
    default=[hid for hid in default_available if any(h == hid for h, _ in _HEROES)],
    format_func=lambda hid: next((n for h, n in _HEROES if h == hid), hid),
    key="playground_available",
)


def _hero_form(hid: str) -> dict[str, Any]:
    """Return ``{state_key: value}`` overrides for one hero."""
    name = next((n for h, n in _HEROES if h == hid), hid)
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
                help="6 tiers per star × 5 stars = 30 total advance slots.",
            )
            out[f"heroes.entries.{hid}.star_progress"] = int(star_progress)
        with c3:
            shards = st.number_input(
                "shards_current (in stockpile)",
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


hero_overrides: dict[str, Any] = {}
for hid in available_ids:
    hero_overrides.update(_hero_form(hid))


# -------------------------------------------------------------------------
# Compose synthetic flat-state
# -------------------------------------------------------------------------

state_flat: dict[str, Any] = {
    "chief.furnace_level": furnace_level,
    "account.drill_camp_unlocked": bool(drill_camp),
    "resources.hero_xp": hero_xp,
    "resources.diamond": gems,
}
for k, v in manual_inputs.items():
    state_flat[f"resources.{k}"] = v
state_flat.update(hero_overrides)


# -------------------------------------------------------------------------
# Solve + plan on the synthetic state
# -------------------------------------------------------------------------

result, prune, breakdowns = solve_optimal(state_flat, ctx, server_age_days=server_age)
plan = plan_top_k(state_flat, ctx, k=plan_k, server_age_days=server_age)
capacities = compute_capacities(state_flat, ctx)

selected_ids = set(result.chosen_ids)
selected_count = len(result.selected)
kept_count = len(prune.kept)
pruned_count = len(prune.dropped)
rejected_count = kept_count - selected_count


# -------------------------------------------------------------------------
# Dashboard strip
# -------------------------------------------------------------------------

m1, m2, m3, m4, m5, m6 = st.columns(6)
status_emoji = "🟢" if result.status in ("OPTIMAL", "FEASIBLE") else "🟠"
m1.metric("Solver", f"{status_emoji} {result.status}")
m2.metric("Objective", f"{result.objective_value:,}")
m3.metric("Selected", selected_count)
m4.metric("Rejected", rejected_count)
m5.metric("Pruned", pruned_count)
m6.metric("Profile", profile_id)

_profile_desc = str(ctx.active_profile.get("description") or "").strip()
if _profile_desc:
    st.caption(f"📌 {_profile_desc}")


# -------------------------------------------------------------------------
# Helpers (shared shape with optimizer_debug)
# -------------------------------------------------------------------------

def _command_label(c: Candidate) -> tuple[str, str, str]:
    detail = ""
    from_disp = c.payload.get("from_level")
    to_disp = c.payload.get("to_level")
    if c.action == "star_tier_up":
        from_disp = c.payload.get("from_progress")
        to_disp = c.payload.get("to_progress")
        detail = f"★{c.payload.get('star_level')} t{c.payload.get('tier_in_star')}"
    elif c.action == "skill_up":
        detail = f"{c.payload.get('track')}.{c.payload.get('slot')}"
    return (
        detail,
        "" if from_disp is None else str(from_disp),
        "" if to_disp is None else str(to_disp),
    )


def _cost_str(c: Candidate) -> str:
    return ", ".join(f"{cost.amount} {cost.resource}" for cost in c.costs) or "—"


def _reasons_for(c: Candidate, br: ScoreBreakdown, *, is_selected: bool | None = None) -> list[str]:
    return generate_reasons(c, br, ctx, is_selected=is_selected)


# -------------------------------------------------------------------------
# Tabs (read-only — no Approve / Queue buttons)
# -------------------------------------------------------------------------

tab_queue, tab_candidates, tab_resources, tab_score, tab_state = st.tabs(
    ["Plan", "Candidates", "Resources", "Score Breakdown", "Synth state"]
)


with tab_queue:
    if not plan:
        st.info("Planner returned no steps — bump some budgets in the sidebar.")
    else:
        rows: list[dict[str, Any]] = []
        for i, step in enumerate(plan, start=1):
            c = step.candidate
            br = step.breakdown
            detail, from_repr, to_repr = _command_label(c)
            rows.append(
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
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        st.divider()
        st.subheader("Resource diff after K steps")
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
    cand_rows: list[dict[str, Any]] = []
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
                "reasons": ", ".join(_reasons_for(c, br, is_selected=is_sel)) if br else "",
                "drop_reason": "" if is_sel else (rejection_reason(c, br) if br else ""),
            }
        )
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
        st.info("Nothing scored — add some heroes / budgets.")
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
            bd_rows.append({"component": "threshold_bonus", "value": round(br.threshold_bonus, 2)})
        if br.replacement_penalty:
            bd_rows.append({"component": "−replacement_penalty", "value": -round(br.replacement_penalty, 2)})
        if br.resource_rarity_penalty:
            bd_rows.append({"component": "−resource_penalty", "value": -round(br.resource_rarity_penalty, 2)})
        bd_rows.append({"component": "final", "value": round(br.final_score, 2)})
        bd_df = pd.DataFrame(bd_rows)
        st.bar_chart(bd_df.set_index("component"))


with tab_state:
    st.caption(
        "The synthesised flat-state passed to the optimizer. Copy as YAML "
        "if you want to pin it as a test fixture."
    )
    st.json(state_flat)

    import yaml as _yaml

    st.download_button(
        "⬇️ Download synth state as YAML",
        data=_yaml.safe_dump(state_flat, sort_keys=True, allow_unicode=True),
        file_name="playground_state.yaml",
        mime="application/x-yaml",
    )
