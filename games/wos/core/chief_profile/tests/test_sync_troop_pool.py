"""Unit tests for the troop-pool parse (``parse_troop_cells`` / ``_parse_cell``).

Inputs are the exact OCR texts observed on-device for the Troops Preview grid
(2 columns × 6 rows, sorted descending). The reader takes the max count per type
(top tier ≈ that type's total).
"""

from __future__ import annotations

from games.wos.core.chief_profile.sync_troop_pool import (
    _NAME_TOPS,
    _NAME_TOPS_SUB,
    _TAB_CONFIG,
    _parse_cell,
    parse_troop_cells,
)

# Real on-device OCR of the 12 cells (note the leading punctuation noise + the
# garbled separators the upscaled OCR leaves on the count line).
LIVE_CELLS = [
    "Supreme Infantry\n|73,443",
    "Supreme Lancer\n}|67,290",
    "Supreme Marksman\n‘|34,449",
    "Brave Lancer\n7",
    "Heroic Marksman\n66",
    ": Hardy Infantry\n467",
    "Hardy Lancer\n336",
    "} Hardy Marksman\n| 381",
    "| Veteran Infantry\n1",
    "| | Veteran Marksman\n198",
    "| Senior Lancer\n3",
    "| | Senior Marksman\n}16",
]


def test_parse_cells_takes_max_per_type():
    assert parse_troop_cells(LIVE_CELLS) == {
        "infantry": 73443,
        "lancer": 67290,
        "marksman": 34449,
    }


def test_parse_cell_type_from_first_line_count_largest():
    assert _parse_cell("Supreme Infantry\n|73,443") == ("infantry", 73443)
    assert _parse_cell("} Hardy Marksman\n| 381") == ("marksman", 381)


def test_parse_cell_fuzzy_type_tolerates_ocr_errors():
    # "Inrantry" (f→r) still resolves to infantry.
    typ, num = _parse_cell("Supreme Inrantry\n12,345")
    assert typ == "infantry"
    assert num == 12345


def test_parse_cell_handles_missing_type_or_count():
    assert _parse_cell("") == (None, 0)
    assert _parse_cell("Total Troops") == (None, 0)
    assert _parse_cell("Supreme Lancer\n") == ("lancer", 0)


def test_tab_config_maps_geometry_and_key_suffix():
    # All → total (grid right under the bar); City → available (at home);
    # Wilderness → deployed. City/Wilderness use the shifted sub-header geometry.
    assert _TAB_CONFIG["all"] == (_NAME_TOPS, "total")
    assert _TAB_CONFIG["city"] == (_NAME_TOPS_SUB, "available")
    assert _TAB_CONFIG["wilderness"] == (_NAME_TOPS_SUB, "wilderness")
    # The sub-header tabs push the grid strictly lower than the All tab.
    assert _NAME_TOPS_SUB[0] > _NAME_TOPS[0]
