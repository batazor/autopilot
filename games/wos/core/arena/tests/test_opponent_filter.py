"""Paid exclude-own-alliance Arena filter: tag normalization + skip policy."""
from __future__ import annotations

import pytest
from games.wos.core.arena.opponent_filter import (
    SETTING_KEY,
    TargetingPlan,
    TargetVerdict,
    is_own_alliance,
    normalize_tag,
    plan_targets,
    should_skip_opponent,
    tag_from_display_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[RoSe]", "ROSE"),
        (" abc ", "ABC"),
        ("[A-B_1]", "AB1"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_tag(raw, expected):
    assert normalize_tag(raw) == expected


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("[Rxt]resion", "RXT"),        # real challenge-list labels
        ("[ZPp]palangsae0", "ZPP"),
        ("[EQY]Walhalla 11", "EQY"),
        ("[Gfd]冷やしたぬき", "GFD"),  # CJK nickname, tag still clean
        ("(ABC)name", "ABC"),          # OCR misread the bracket glyph
        ("NoTagNick", ""),             # un-tagged name -> no tag
        ("[toolongtag]x", ""),         # >4 chars isn't a tag prefix
        ("", ""),
        (None, ""),
    ],
)
def test_tag_from_display_name(display, expected):
    assert tag_from_display_name(display) == expected


def test_is_own_alliance_matches_regardless_of_brackets_and_case():
    own = ["ROSE", "blue"]
    assert is_own_alliance("[rose]", own)
    assert is_own_alliance("BLUE", own)
    assert not is_own_alliance("[ENMY]", own)


def test_unreadable_opponent_tag_is_not_treated_as_own():
    # When in doubt, don't suppress: a missed skip is one extra fight; a wrong
    # skip wastes a challenge.
    assert not is_own_alliance("", ["ROSE"])
    assert not is_own_alliance(None, ["ROSE"])


def test_disabled_never_skips_even_own_alliance():
    v = should_skip_opponent("[ROSE]allyman", ["ROSE"], enabled=False)
    assert v == TargetVerdict(False, "filter_disabled")


def test_enabled_skips_own_alliance_ignoring_nickname():
    # The label is the full OCR'd "[TAG]Nickname" — only the tag should decide.
    v = should_skip_opponent("[ROSE]allyman", ["ROSE", "BLUE"], enabled=True)
    assert v == TargetVerdict(True, "own_alliance")


def test_enabled_fights_enemy():
    v = should_skip_opponent("[ENMY]rando", ["ROSE"], enabled=True)
    assert v == TargetVerdict(False, "enemy")


def test_enabled_untagged_or_unreadable_name_fights():
    assert should_skip_opponent("", ["ROSE"], enabled=True) == TargetVerdict(False, "enemy")
    assert should_skip_opponent("GuildlessNick", ["ROSE"], enabled=True) == TargetVerdict(False, "enemy")


def test_seam_constants():
    # 2-level planner key (3-level would silently no-op in the state store).
    assert SETTING_KEY == "planner.arena_exclude_own_alliance"
    assert SETTING_KEY.count(".") == 1


# --- plan_targets: skip-row, then refresh when nothing is fightable ----------

ROSTER = ["[Rxt]resion", "[ZPp]palangsae0", "[EQY]Walhalla 11", "[MIR]ASKYUUAI", "[Gfd]x"]


def test_disabled_fights_top_row_regardless_of_tags():
    plan = plan_targets(ROSTER, ["Rxt"], enabled=False)
    assert plan == TargetingPlan("fight", 0, (), "filter_disabled")


def test_enabled_fights_first_enemy_when_top_is_own():
    # Own = Rxt -> skip row 0, fight row 1.
    plan = plan_targets(ROSTER, ["RXT"], enabled=True)
    assert plan == TargetingPlan("fight", 1, (0,), "enemy")


def test_enabled_fights_top_when_top_is_enemy():
    plan = plan_targets(ROSTER, ["EQY"], enabled=True)
    assert plan == TargetingPlan("fight", 0, (), "enemy")


def test_all_own_refreshes_when_allowed():
    own = ["Rxt", "ZPp", "EQY", "MIR", "Gfd"]
    plan = plan_targets(ROSTER, own, enabled=True, can_refresh=True)
    assert plan == TargetingPlan("refresh", None, (0, 1, 2, 3, 4), "all_own_refresh")


def test_all_own_stops_when_refreshes_spent():
    own = ["Rxt", "ZPp", "EQY", "MIR", "Gfd"]
    plan = plan_targets(ROSTER, own, enabled=True, can_refresh=False)
    assert plan == TargetingPlan("stop", None, (0, 1, 2, 3, 4), "all_own_no_refresh")


def test_blank_rows_are_ignored():
    # Fewer than 5 readable opponents (trailing empty rows); own = first one.
    plan = plan_targets(["[ROSE]ally", "", "[ENMY]foe", None], ["ROSE"], enabled=True)
    assert plan == TargetingPlan("fight", 2, (0,), "enemy")


def test_no_readable_rows_stops():
    assert plan_targets(["", None, "  "], ["ROSE"], enabled=True) == TargetingPlan(
        "stop", None, (), "no_opponents"
    )


def test_empty_own_tags_never_skips():
    # We don't know our own alliance -> nothing is "own", fight the top row.
    plan = plan_targets(ROSTER, [], enabled=True)
    assert plan == TargetingPlan("fight", 0, (), "enemy")
