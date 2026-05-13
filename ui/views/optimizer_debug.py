"""Debug view for the upgrade-action optimizer.

Picks a gamer from ``db/state.yaml``, generates candidate upgrades for
their current state, ranks them through :mod:`optimizer`, and shows the
ranked table + score breakdown. Useful for sanity-checking balance edits
on the ``Config → Balance`` page without running the bot.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from config.state_store import get_state_store
from optimizer import load_balance_context, rank_candidates
from optimizer.context import invalidate_balance_context


st.title("Optimizer · ranked upgrade candidates")
st.caption(
    "Reads `config/balance/*.yaml` + `db/state.yaml`, produces next-step "
    "candidates, ranks them with the scorer. MVP shows `level_up` only — "
    "more action types land as we extend the candidate generator."
)


# --- Pick gamer + tunables ------------------------------------------------
col_gamer, col_age, col_refresh = st.columns([2, 1, 1], vertical_alignment="bottom")

store = get_state_store()
db = store._db  # mid-level access — UI is read-only here
gamers = list(db.gamers)
if not gamers:
    st.warning("`db/state.yaml` has no gamers yet. Add one first.")
    st.stop()

with col_gamer:
    gamer_idx = st.selectbox(
        "Gamer",
        options=range(len(gamers)),
        format_func=lambda i: f"{gamers[i].nickname or '(no nick)'} · `{gamers[i].id}`",
        key="optimizer_debug_gamer",
    )
    gamer = gamers[gamer_idx]

with col_age:
    server_age = st.number_input(
        "server_age_days",
        min_value=0,
        max_value=400,
        value=14,
        step=1,
        key="optimizer_debug_age",
    )

with col_refresh:
    if st.button("🔄 Reload balance", use_container_width=True):
        invalidate_balance_context()
        st.success("Balance cache cleared.")


ctx = load_balance_context()
active_profile = ctx.active_profile_id or "(none)"
st.markdown(
    f"**Active profile:** `{active_profile}` · "
    f"objective_weights: {ctx.active_profile.get('objective_weights') or {}}"
)
st.caption("Change in `Config → Balance → Profiles`.")


# --- Rank -----------------------------------------------------------------

state_flat = store.get_or_create(str(gamer.id), nickname=gamer.nickname).to_flat_dict()
ranked = rank_candidates(state_flat, ctx, server_age_days=int(server_age))

if not ranked:
    st.info(
        "No candidates generated. Likely causes: no heroes flagged "
        "`available=True` in state, or no cost table covers this state. "
        "Run a `scan_heroes_grid` overlay to populate the entries."
    )
    st.stop()

rows: list[dict[str, Any]] = []
for c, br in ranked:
    # Cards differ by action: level_up has from/to_level; star_tier_up has
    # star_level/tier_in_star + from/to_progress; skill_up has track.slot
    # + from/to_level. Surface the most meaningful fields per action.
    from_disp = c.payload.get("from_level")
    to_disp = c.payload.get("to_level")
    detail = ""
    if c.action == "star_tier_up":
        from_disp = c.payload.get("from_progress")
        to_disp = c.payload.get("to_progress")
        detail = f"★{c.payload.get('star_level')} t{c.payload.get('tier_in_star')}"
    elif c.action == "skill_up":
        detail = f"{c.payload.get('track')}.{c.payload.get('slot')}"
    row = {
        "rank": len(rows) + 1,
        "id": c.id,
        "action": c.action,
        "hero": c.hero_id or "",
        "score": round(br.final_score, 1),
        "base": round(br.base_value, 1),
        "gain": round(br.upgrade_gain, 2),
        "thresh": round(br.threshold_bonus, 0) if br.threshold_bonus else 0,
        "penalty": round(br.replacement_penalty + br.resource_rarity_penalty, 1),
        "detail": detail,
        "from": from_disp,
        "to": to_disp,
        "cost": c.costs[0].amount if c.costs else 0,
        "resource": c.costs[0].resource if c.costs else "",
        "band": c.priority_band,
    }
    rows.append(row)

df = pd.DataFrame(rows)
st.dataframe(df, width="stretch", hide_index=True)


# --- Drill-down -----------------------------------------------------------

st.divider()
st.subheader("Inspect candidate")
opt_ids = [c.id for c, _ in ranked]
sel = st.selectbox(
    "Candidate",
    options=opt_ids,
    key="optimizer_debug_inspect",
)
chosen = next(((c, br) for c, br in ranked if c.id == sel), None)
if chosen is None:
    st.stop()
cand, br = chosen

cleft, cright = st.columns(2)
with cleft:
    st.markdown("**Action**")
    st.json(
        {
            "id": cand.id,
            "action": cand.action,
            "hero": cand.hero_id,
            "priority_band": cand.priority_band,
            "costs": [{"resource": x.resource, "amount": x.amount} for x in cand.costs],
            "preconditions": list(cand.preconditions),
            "payload": cand.payload,
        }
    )
with cright:
    st.markdown("**Score breakdown**")
    st.json(
        {
            "final_score": round(br.final_score, 3),
            "base_value": round(br.base_value, 3),
            "upgrade_gain": round(br.upgrade_gain, 3),
            "threshold_bonus": round(br.threshold_bonus, 3),
            "mode_contributions": {k: round(v, 2) for k, v in br.mode_contributions.items()},
            "replacement_penalty": round(br.replacement_penalty, 3),
            "replacement_risk": round(br.replacement_risk, 4),
            "sunkness": round(br.sunkness, 4),
            "resource_rarity_penalty": round(br.resource_rarity_penalty, 3),
            "resource_contributions": {
                k: round(v, 3) for k, v in br.resource_contributions.items()
            },
            "notes": list(br.notes),
        }
    )

with st.expander("Active hero meta", expanded=False):
    if cand.hero_id:
        st.json(ctx.hero_meta(cand.hero_id))
    else:
        st.caption("(global rule — no hero meta)")
