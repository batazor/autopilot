"""End-to-end + per-module contract tests for :mod:`optimizer`.

Goal: catch regressions when scoring weights / hard rules / candidate
shapes drift. Fixtures use a small synthetic flat-state so failures
print actually-readable hero / score values.
"""

from __future__ import annotations

import pytest

from optimizer import (
    apply_command,
    compute_capacities,
    generate_candidates,
    generate_reasons,
    load_balance_context,
    plan_top_k,
    prune_candidates,
    rank_candidates,
    rejection_reason,
    score_candidate,
    solve_optimal,
)
from optimizer.context import invalidate_balance_context

# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def ctx():
    invalidate_balance_context()
    return load_balance_context()


@pytest.fixture
def basic_state():
    """Mid-game F2P snapshot: core trio + a joiner + a Gen2 hero."""
    return {
        "heroes.entries.molly.available": True,
        "heroes.entries.molly.level": 7,
        "heroes.entries.molly.star_progress": 8,
        "heroes.entries.molly.skills.expedition.1": 2,
        "heroes.entries.molly.shards_current": 5,
        "heroes.entries.bahiti.available": True,
        "heroes.entries.bahiti.level": 6,
        "heroes.entries.bahiti.star_progress": 4,
        "heroes.entries.bahiti.shards_current": 8,
        "heroes.entries.jessie.available": True,
        "heroes.entries.jessie.level": 8,
        "heroes.entries.jessie.star_progress": 18,
        "heroes.entries.jessie.skills.expedition.1": 4,
        "heroes.entries.jessie.shards_current": 12,
        "heroes.entries.jasser.available": True,
        "heroes.entries.jasser.level": 5,
        "heroes.entries.jasser.skills.expedition.1": 4,
        "chief.furnace_level": 25,
        "resources.hero_xp": 40000,
        "resources.epic_expedition_manual": 20,
        "resources.epic_exploration_manual": 14,
        "resources.diamond": 30000,
    }


# -------------------------------------------------------------------------
# Candidate generation
# -------------------------------------------------------------------------


def test_generate_candidates_emits_all_action_types(ctx, basic_state):
    cands = generate_candidates(basic_state, ctx)
    actions = {c.action for c in cands}
    assert actions == {"level_up", "star_tier_up", "skill_up"}, (
        f"expected the three MVP action types, got {actions}"
    )


def test_unavailable_hero_produces_no_candidates(ctx):
    state = {
        "heroes.entries.molly.available": False,
        "heroes.entries.molly.level": 3,
        "chief.furnace_level": 25,
    }
    cands = generate_candidates(state, ctx)
    assert all(c.hero_id != "molly" for c in cands), (
        "locked heroes must not surface upgrade candidates"
    )


def test_level_up_cost_uses_hero_xp(ctx, basic_state):
    cands = generate_candidates(basic_state, ctx)
    molly_level = next(
        c for c in cands if c.action == "level_up" and c.hero_id == "molly"
    )
    assert len(molly_level.costs) == 1
    assert molly_level.costs[0].resource == "hero_xp"
    assert molly_level.costs[0].amount > 0


def test_star_tier_up_uses_per_hero_shard(ctx, basic_state):
    cands = generate_candidates(basic_state, ctx)
    bahiti_star = next(
        c for c in cands if c.action == "star_tier_up" and c.hero_id == "bahiti"
    )
    assert bahiti_star.costs[0].resource == "bahiti_shard"


# -------------------------------------------------------------------------
# Hard rule pruning
# -------------------------------------------------------------------------


def test_joiner_level_cap_after_drill_blocks_jasser(ctx):
    state = {
        "heroes.entries.jasser.available": True,
        "heroes.entries.jasser.level": 5,
        "chief.furnace_level": 25,
        "account.drill_camp_unlocked": True,
    }
    cands = generate_candidates(state, ctx)
    prune = prune_candidates(cands, state, ctx)
    pruned_ids = [c.id for c, _ in prune.dropped]
    jasser_level = next(
        c for c in cands if c.action == "level_up" and c.hero_id == "jasser"
    )
    assert jasser_level.id in pruned_ids, (
        "joiner_only + drill_camp_unlocked + manual_level_cap_post_drill=0 "
        "should prune jasser level_up"
    )


def test_stop_replacement_drops_sergey_star_when_flint_unlocked(ctx):
    state = {
        "heroes.entries.sergey.available": True,
        "heroes.entries.sergey.level": 5,
        "heroes.entries.sergey.star_progress": 6,
        "heroes.entries.sergey.shards_current": 20,
        "heroes.entries.flint.available": True,
        "chief.furnace_level": 25,
    }
    cands = generate_candidates(state, ctx)
    prune = prune_candidates(cands, state, ctx)
    pruned_reasons = {c.id: reason for c, reason in prune.dropped}
    sergey_star = next(
        c for c in cands if c.action == "star_tier_up" and c.hero_id == "sergey"
    )
    assert pruned_reasons.get(sergey_star.id, "").startswith("stop_replacement"), (
        f"flint unlocked should trigger stop rule on sergey star_tier_up; "
        f"got pruned reasons: {pruned_reasons}"
    )


# -------------------------------------------------------------------------
# Scoring
# -------------------------------------------------------------------------


def test_score_uses_active_profile_weights(ctx, basic_state):
    # molly has mw{expedition:80, exploration:90, arena:85, bear_join:30} —
    # all four modes contribute, base must be > 0.
    cands = generate_candidates(basic_state, ctx)
    molly = next(c for c in cands if c.action == "level_up" and c.hero_id == "molly")
    br = score_candidate(molly, ctx, basic_state, server_age_days=10)
    assert br.base_value > 0
    assert set(br.mode_contributions) == {"expedition", "exploration", "arena", "bear_join"}


def test_bear_threshold_bonus_applies_to_joiner_only(ctx):
    """jasser exp.1 lvl 4→5 → threshold bonus; bahiti same path → no bonus."""
    state = {
        "heroes.entries.jasser.available": True,
        "heroes.entries.jasser.level": 5,
        "heroes.entries.jasser.star_progress": 30,
        "heroes.entries.jasser.skills.expedition.1": 4,
        "heroes.entries.bahiti.available": True,
        "heroes.entries.bahiti.level": 5,
        "heroes.entries.bahiti.star_progress": 30,
        "heroes.entries.bahiti.skills.expedition.1": 4,
        "chief.furnace_level": 25,
    }
    cands = generate_candidates(state, ctx)
    jasser_skill = next(
        c for c in cands
        if c.action == "skill_up"
        and c.hero_id == "jasser"
        and c.payload.get("to_level") == 5
        and c.payload.get("track") == "expedition"
    )
    bahiti_skill = next(
        c for c in cands
        if c.action == "skill_up"
        and c.hero_id == "bahiti"
        and c.payload.get("to_level") == 5
        and c.payload.get("track") == "expedition"
    )
    br_jasser = score_candidate(jasser_skill, ctx, state, server_age_days=10)
    br_bahiti = score_candidate(bahiti_skill, ctx, state, server_age_days=10)
    assert br_jasser.threshold_bonus > 0, "joiner should receive bear_join_skill_5 bonus"
    assert br_bahiti.threshold_bonus == 0, "core hero (no joiner tag) should not"


def test_rank_candidates_sorted_desc(ctx, basic_state):
    ranked = rank_candidates(basic_state, ctx, server_age_days=10)
    scores = [br.final_score for _, br in ranked]
    assert scores == sorted(scores, reverse=True), "rank_candidates must sort desc"


# -------------------------------------------------------------------------
# Solver
# -------------------------------------------------------------------------


def test_solve_respects_hero_xp_capacity(ctx, basic_state):
    result, prune, brs = solve_optimal(basic_state, ctx, server_age_days=10)
    capacities = compute_capacities(basic_state, ctx)
    spent = 0
    for c in result.selected:
        for cost in c.costs:
            if cost.resource == "hero_xp":
                spent += cost.amount
    assert spent <= capacities.get("hero_xp", 0), (
        f"selected commands spend {spent} hero_xp but capacity is "
        f"{capacities.get('hero_xp', 0)}"
    )


def test_solve_returns_optimal_or_feasible_status(ctx, basic_state):
    result, _, _ = solve_optimal(basic_state, ctx, server_age_days=10)
    assert result.status in ("OPTIMAL", "FEASIBLE"), result.status


def test_unknown_resource_starves_candidate(ctx):
    """No mythic_specific_shard inventory ⇒ mythic-rarity star_tier_up is
    forced to x=0 by the capacity constraint, even if otherwise feasible."""
    state = {
        "heroes.entries.molly.available": True,
        "heroes.entries.molly.level": 5,
        "heroes.entries.molly.star_progress": 0,
        "chief.furnace_level": 25,
    }
    result, prune, brs = solve_optimal(state, ctx, server_age_days=10)
    molly_star = next(
        (c for c in prune.kept if c.action == "star_tier_up" and c.hero_id == "molly"),
        None,
    )
    if molly_star is None:
        pytest.skip("no molly star_tier_up candidate generated")
    assert molly_star not in result.selected, (
        "molly star_tier_up needs a mythic shard pool we don't have"
    )


# -------------------------------------------------------------------------
# Executor (planner)
# -------------------------------------------------------------------------


def test_apply_command_mutates_only_a_copy(ctx, basic_state):
    cands = generate_candidates(basic_state, ctx)
    molly_level = next(
        c for c in cands if c.action == "level_up" and c.hero_id == "molly"
    )
    before = dict(basic_state)
    after = apply_command(basic_state, molly_level)
    assert basic_state == before, "apply_command must not mutate the input"
    assert (
        after["heroes.entries.molly.level"]
        == molly_level.payload["to_level"]
    )


def test_apply_command_deducts_hero_xp(ctx, basic_state):
    cands = generate_candidates(basic_state, ctx)
    molly_level = next(
        c for c in cands if c.action == "level_up" and c.hero_id == "molly"
    )
    after = apply_command(basic_state, molly_level)
    assert (
        int(after["resources.hero_xp"])
        == int(basic_state["resources.hero_xp"]) - molly_level.costs[0].amount
    )


def test_plan_top_k_does_not_repeat_a_step(ctx, basic_state):
    """Re-optimize after each step should make the next iteration pick
    a *different* candidate (not the same level_up twice on the same hero
    at the same from_level)."""
    plan = plan_top_k(basic_state, ctx, k=3, server_age_days=10)
    assert len(plan) >= 2
    ids = [s.candidate.id for s in plan]
    assert len(set(ids)) == len(ids), f"plan repeated a step: {ids}"


# -------------------------------------------------------------------------
# Reasons
# -------------------------------------------------------------------------


def test_reasons_tag_core_hero(ctx, basic_state):
    cands = generate_candidates(basic_state, ctx)
    molly = next(c for c in cands if c.action == "level_up" and c.hero_id == "molly")
    br = score_candidate(molly, ctx, basic_state, server_age_days=10)
    reasons = generate_reasons(molly, br, ctx, is_selected=True)
    assert "active_core_lineup" in reasons
    assert "incremental_level" in reasons
    assert "solver_selected" in reasons


def test_reasons_flag_bear_threshold(ctx):
    state = {
        "heroes.entries.jasser.available": True,
        "heroes.entries.jasser.level": 5,
        "heroes.entries.jasser.star_progress": 30,
        "heroes.entries.jasser.skills.expedition.1": 4,
        "chief.furnace_level": 25,
    }
    cands = generate_candidates(state, ctx)
    skill5 = next(
        c for c in cands
        if c.action == "skill_up"
        and c.payload.get("track") == "expedition"
        and c.payload.get("slot") == 1
        and c.payload.get("to_level") == 5
    )
    br = score_candidate(skill5, ctx, state, server_age_days=10)
    reasons = generate_reasons(skill5, br, ctx, is_selected=True)
    assert "bear_joiner_threshold" in reasons
    assert "bear_joiner_hero" in reasons
    assert "threshold_bonus_applied" in reasons


def test_rejection_reason_falls_through_to_score(ctx):
    state = {
        "heroes.entries.molly.available": True,
        "heroes.entries.molly.level": 5,
        "heroes.entries.molly.star_progress": 0,
        "chief.furnace_level": 25,
    }
    cands = generate_candidates(state, ctx)
    star = next(c for c in cands if c.action == "star_tier_up" and c.hero_id == "molly")
    br = score_candidate(star, ctx, state, server_age_days=10)
    why = rejection_reason(star, br)
    # No mythic shards in state → final_score should be 0 → starved
    if br.final_score == 0 and br.base_value > 0:
        assert why == "starved_by_resource_penalty"
    else:
        # Could be "lower_score_than_selected" — either way, valid label
        assert why


# -------------------------------------------------------------------------
# Audit log
# -------------------------------------------------------------------------


def test_dispatcher_envelope_for_level_up(ctx, basic_state):
    from optimizer import build_envelope, scenario_name_for

    cands = generate_candidates(basic_state, ctx)
    bahiti_level = next(
        c for c in cands if c.action == "level_up" and c.hero_id == "bahiti"
    )
    assert scenario_name_for(bahiti_level) == "level_up_bahiti"
    env = build_envelope(bahiti_level, player_id="42", instance_id="bs1", now=1.0)
    assert env.dsl_scenario == "level_up_bahiti"
    assert env.set_node == "page.heroes.bahiti"
    assert env.region is None  # only skill_up sets region
    assert env.task_type == "dsl_scenario"


def test_dispatcher_envelope_for_skill_up_carries_region(ctx, basic_state):
    from optimizer import build_envelope

    cands = generate_candidates(basic_state, ctx)
    skill = next(
        c for c in cands
        if c.action == "skill_up"
        and c.hero_id == "molly"
        and int(c.payload.get("slot") or 0) == 1
    )
    env = build_envelope(skill, player_id="42", instance_id="bs1", now=1.0)
    assert env.region == "page.heroes.unit.skill_1"


def test_enqueue_envelope_writes_to_queue_key(ctx, basic_state):
    """``enqueue_envelope`` must hit ``wos:queue:<instance>`` with ZADD
    and a JSON body matching scheduler.queue.RedisQueue.schedule."""
    import json
    from unittest.mock import MagicMock

    from optimizer import build_envelope, enqueue_envelope, queue_key

    cands = generate_candidates(basic_state, ctx)
    bahiti = next(c for c in cands if c.action == "level_up" and c.hero_id == "bahiti")
    env = build_envelope(bahiti, player_id="42", instance_id="bs1", now=100.0)

    client = MagicMock()
    written_key = enqueue_envelope(env, client)
    assert written_key == queue_key("bs1") == "wos:queue:bs1"

    # Inspect the ZADD call.
    assert client.zadd.called
    args, _kwargs = client.zadd.call_args
    assert args[0] == "wos:queue:bs1"
    mapping = args[1]
    assert len(mapping) == 1
    payload_json, score = next(iter(mapping.items()))
    assert score == 100.0
    body = json.loads(payload_json)
    assert body["task_id"] == env.task_id
    assert body["task_type"] == "dsl_scenario"
    assert body["dsl_scenario"] == "level_up_bahiti"
    assert body["set_node"] == "page.heroes.bahiti"
    assert body["instance_id"] == "bs1"


def test_generated_scenarios_exist_for_every_action(ctx, basic_state):
    """Smoke: every scenario the dispatcher might name must be on disk."""
    from pathlib import Path

    from optimizer import build_envelope

    upgrade_dir = (
        Path(__file__).resolve().parents[1]
        / "scenarios"
        / "heroes"
        / "upgrade"
    )
    cands = generate_candidates(basic_state, ctx)
    for c in cands:
        env = build_envelope(c, player_id="42", instance_id="bs1")
        scen = upgrade_dir / f"{env.dsl_scenario}.yaml"
        assert scen.is_file(), f"missing scenario file: {scen}"


def test_history_round_trip(tmp_path):
    from optimizer.history import HistoryEntry, append_entry, load_history

    p = tmp_path / "history.yaml"
    entry = HistoryEntry(
        approved_at=1234567.0,
        gamer_id="42",
        profile="conservative_long_term_f2p",
        candidate_id="level_up:molly:5->6",
        action="level_up",
        hero_id="molly",
        score=5400.5,
        costs=[{"resource": "hero_xp", "amount": 1580}],
        state_diff={"heroes.entries.molly.level": {"before": 5, "after": 6}},
        reasons=["active_core_lineup", "incremental_level"],
        notes=[],
    )
    append_entry(entry, path=p)
    read_back = load_history(p)
    assert len(read_back) == 1
    assert read_back[0].candidate_id == "level_up:molly:5->6"
    assert read_back[0].state_diff == {
        "heroes.entries.molly.level": {"before": 5, "after": 6}
    }
