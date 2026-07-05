"""Validation rules of the community-scene JSON importer."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "import_scene_json.py"
_spec = importlib.util.spec_from_file_location("import_scene_json", _TOOL)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _scene(**over):
    data = {
        "type": "dreamscape_scene",
        "slug": "frost-harbor",
        "title": "Frost Harbor",
        "season": 3,
        "points": [
            {"n": 1, "name": "Book", "xPct": 32.59, "yPct": 38.93},
            {"n": 2, "name": "Pocket Watch", "xPct": 51.39, "yPct": 72.42},
        ],
    }
    data.update(over)
    return data


def test_valid_payload_round_trips():
    slug, title, season, points = _mod._validate(_scene())
    assert (slug, title, season) == ("frost-harbor", "Frost Harbor", 3)
    assert points == [
        {"n": 1, "name": "Book", "xPct": 32.59, "yPct": 38.93},
        {"n": 2, "name": "Pocket Watch", "xPct": 51.39, "yPct": 72.42},
    ]


def test_points_sorted_by_n_and_names_whitespace_collapsed():
    data = _scene(
        points=[
            {"n": 2, "name": "  Pocket   Watch ", "xPct": 1, "yPct": 2},
            {"n": 1, "name": "Book", "xPct": 3, "yPct": 4},
        ]
    )
    _, _, _, points = _mod._validate(data)
    assert [p["n"] for p in points] == [1, 2]
    assert points[1]["name"] == "Pocket Watch"


def test_title_defaults_from_slug():
    _, title, _, _ = _mod._validate(_scene(title=""))
    assert title == "Frost Harbor"


@pytest.mark.parametrize(
    "over",
    [
        {"type": "regions"},                       # zones export, not a scene
        {"slug": "Frost Harbor"},                  # not kebab-case
        {"slug": ""},
        {"season": "three"},
        {"points": []},
        {"points": [{"n": 1, "name": "", "xPct": 1, "yPct": 1}]},          # empty name
        {"points": [{"n": 1, "name": "Book", "xPct": 101, "yPct": 1}]},    # out of range
        {"points": [{"n": 1, "name": "Book"}]},                            # missing coords
        {
            "points": [                                                    # duplicate n
                {"n": 1, "name": "Book", "xPct": 1, "yPct": 1},
                {"n": 1, "name": "Axe", "xPct": 2, "yPct": 2},
            ]
        },
        {
            "points": [                                                    # duplicate name
                {"n": 1, "name": "Book", "xPct": 1, "yPct": 1},
                {"n": 2, "name": "book", "xPct": 2, "yPct": 2},
            ]
        },
    ],
)
def test_rejects_bad_payloads(over, capsys):
    with pytest.raises(SystemExit):
        _mod._validate(_scene(**over))
