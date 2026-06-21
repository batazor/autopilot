"""Per-account options registry: schema invariants, coercion, value views."""
from __future__ import annotations

import pytest
from games.wos.core.account_options import (
    ACCOUNT_OPTIONS,
    AccountOption,
    Choice,
    coerce_value,
    current_value,
    option_by_key,
    options_for_state,
)
from games.wos.core.arena.opponent_filter import SETTING_KEY as ARENA_KEY

# --- registry invariants (protect every option, present and future) ----------

def test_all_keys_are_two_level_planner_keys():
    # 3-level keys silently no-op in the state store, so the registry forbids them.
    for opt in ACCOUNT_OPTIONS:
        assert opt.key.startswith("planner.")
        assert opt.key.count(".") == 1


def test_keys_are_unique():
    keys = [o.key for o in ACCOUNT_OPTIONS]
    assert len(keys) == len(set(keys))


def test_arena_option_is_registered():
    opt = option_by_key(ARENA_KEY)
    assert opt is not None
    assert opt.type == "bool"
    assert opt.group == "Arena"


def test_bad_key_shape_rejected():
    with pytest.raises(ValueError, match="2-level"):
        AccountOption(key="planner.arena.too.deep", label="x", description="y")
    with pytest.raises(ValueError, match="2-level"):
        AccountOption(key="not_planner", label="x", description="y")


def test_enum_requires_choices():
    with pytest.raises(ValueError, match="choices"):
        AccountOption(key="planner.x", label="x", description="y", type="enum")


# --- coercion ----------------------------------------------------------------

BOOL_OPT = AccountOption(key="planner.flag", label="Flag", description="d")
ENUM_OPT = AccountOption(
    key="planner.mode",
    label="Mode",
    description="d",
    type="enum",
    default="a",
    choices=(Choice("a", "A"), Choice("b", "B")),
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("true", True), ("on", True), ("1", True),
     (1, True), (0, False), ("no", False), ("", False)],
)
def test_bool_coercion(raw, expected):
    assert coerce_value(BOOL_OPT, raw) is expected


def test_enum_coercion_accepts_valid_and_rejects_invalid():
    assert coerce_value(ENUM_OPT, "b") == "b"
    with pytest.raises(ValueError, match="valid choice"):
        coerce_value(ENUM_OPT, "z")


# --- current value + view ----------------------------------------------------

def test_current_value_falls_back_to_default():
    assert current_value(BOOL_OPT, {}) is False
    assert current_value(BOOL_OPT, {"planner.flag": "true"}) is True
    # Corrupt stored enum -> default, not a crash.
    assert current_value(ENUM_OPT, {"planner.mode": "garbage"}) == "a"


def test_options_for_state_carries_values():
    flat = {ARENA_KEY: "true"}
    rows = options_for_state(flat)
    arena = next(r for r in rows if r["key"] == ARENA_KEY)
    assert arena["value"] is True
    assert arena["type"] == "bool"
    assert arena["group"] == "Arena"


def test_options_for_state_has_no_tier_metadata():
    # Licensing/tier gating is gone: rows never carry lock or min_tier keys.
    rows = options_for_state({ARENA_KEY: "true"})
    assert all("locked" not in r for r in rows)
    assert all("min_tier" not in r for r in rows)
