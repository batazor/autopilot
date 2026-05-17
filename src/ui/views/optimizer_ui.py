"""Shared Streamlit helpers for optimizer debug + playground pages."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import streamlit as st

from optimizer import generate_reasons

if TYPE_CHECKING:
    from optimizer.context import BalanceContext
    from optimizer.scorer import ScoreBreakdown
    from optimizer.types import Candidate


def command_label(c: Candidate) -> tuple[str, str, str]:
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
    return (
        detail,
        "" if from_disp is None else str(from_disp),
        "" if to_disp is None else str(to_disp),
    )


def cost_str(c: Candidate) -> str:
    return ", ".join(f"{cost.amount} {cost.resource}" for cost in c.costs) or "—"


def reasons_for(
    c: Candidate,
    br: ScoreBreakdown,
    ctx: BalanceContext,
    *,
    is_selected: bool | None = None,
) -> list[str]:
    return generate_reasons(c, br, ctx, is_selected=is_selected)


def render_optimizer_nav() -> None:
    """Sibling pages — same pattern as ``scenarios.py`` nav row."""
    cols = st.columns([1, 1, 1, 3])
    with cols[0]:
        st.page_link(
            "views/balance.py",
            label="Balance",
            help="Edit scoring profiles, hero meta, and solver defaults.",
            width="stretch",
        )
    with cols[1]:
        st.page_link(
            "views/queue.py",
            label="Queue",
            help="Live worker queue and scheduler history.",
            width="stretch",
        )
    with cols[2]:
        st.page_link(
            "views/player_state.py",
            label="Player state",
            help="Redis + db/state.yaml for all gamers.",
            width="stretch",
        )


def render_solver_metrics(
    *,
    status: str,
    objective: int,
    selected_count: int,
    rejected_count: int,
    pruned_count: int,
    profile_id: str,
    profile_description: str = "",
) -> None:
    """Top metric strip shared by production debug and playground."""
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    status_emoji = "🟢" if status in ("OPTIMAL", "FEASIBLE") else "🟠"
    m1.metric("Solver", f"{status_emoji} {status}")
    m2.metric("Objective", f"{objective:,}")
    m3.metric("Selected", selected_count)
    m4.metric("Rejected", rejected_count)
    m5.metric("Pruned", pruned_count)
    m6.metric("Profile", profile_id or "(none)")
    if profile_description.strip():
        st.caption(f"📌 {profile_description.strip()}")


def candidate_table_rows(
    *,
    prune_kept: list[Candidate],
    prune_dropped: list[tuple[Candidate, str]],
    breakdowns: dict[str, ScoreBreakdown],
    selected_ids: set[str],
    ctx: BalanceContext,
    rejection_reason_fn: Any,
) -> list[dict[str, Any]]:
    """Rows for the Candidates dataframe."""
    rows: list[dict[str, Any]] = []
    for c in prune_kept:
        br = breakdowns.get(c.id)
        is_sel = c.id in selected_ids
        detail, from_repr, to_repr = command_label(c)
        rows.append(
            {
                "id": c.id,
                "hero": c.hero_id or "",
                "action": c.action,
                "detail": detail,
                "from": from_repr,
                "to": to_repr,
                "cost": cost_str(c),
                "score": round(br.final_score, 1) if br else 0.0,
                "status": "selected" if is_sel else "rejected",
                "reasons": ", ".join(reasons_for(c, br, ctx, is_selected=is_sel)) if br else "",
                "drop_reason": "" if is_sel else rejection_reason_fn(c, br),
            }
        )
    for c, reason in prune_dropped:
        detail, from_repr, to_repr = command_label(c)
        rows.append(
            {
                "id": c.id,
                "hero": c.hero_id or "",
                "action": c.action,
                "detail": detail,
                "from": from_repr,
                "to": to_repr,
                "cost": cost_str(c),
                "score": 0.0,
                "status": "pruned",
                "reasons": "",
                "drop_reason": reason,
            }
        )
    return rows
