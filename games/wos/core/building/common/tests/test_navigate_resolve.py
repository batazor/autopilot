"""Planner-id → scanned-map-name resolution for navigate_to_building."""

from games.wos.core.building.common.exec import _resolve_target_name

# Navigator.buildings shape: norm name → (canvas_px, display name).
_MAP = {
    "furnace": ((1.0, 2.0), "Furnace"),
    "hunters hut": ((3.0, 4.0), "Hunters Hut"),
    "iron mine": ((5.0, 6.0), "Iron Mine"),
    "sawmill": ((7.0, 8.0), "Sawmill"),
}


def test_resolves_planner_id_to_map_name():
    # Planner speaks canonical ids; the map has OCR display names.
    assert _resolve_target_name("hunters_hut", _MAP) == "Hunters Hut"
    assert _resolve_target_name("iron_mine", _MAP) == "Iron Mine"
    assert _resolve_target_name("furnace", _MAP) == "Furnace"


def test_resolves_a_display_name_too():
    assert _resolve_target_name("Sawmill", _MAP) == "Sawmill"


def test_unmappable_returns_none():
    # Not in the scanned map → caller falls back to the fuzzy matcher.
    assert _resolve_target_name("embassy", _MAP) is None
    assert _resolve_target_name("totally_unknown_xyz", _MAP) is None
