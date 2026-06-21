"""Unit tests for the Dreamscape onboarding service's pure logic + DB writing."""
from pathlib import Path

import pytest

from api.services import dreamscape_onboarding as svc
from config import dreamscape_db

# ── slugify ──────────────────────────────────────────────────────────────────


def test_slugify() -> None:
    assert svc.slugify("Yard") == "yard"
    assert svc.slugify("Kid's Room") == "kid-s-room"
    assert svc.slugify("  Café (1) ") == "caf-1"
    assert svc.slugify("") == "scene"


# ── parse_name_list ──────────────────────────────────────────────────────────


def test_parse_name_list_mixed_separators() -> None:
    res = svc.parse_name_list("1. Parachutte\n2 Envelope\n26 - Butterfly\n3) Pipe")
    assert res["items"] == [
        {"n": 1, "name": "Parachutte"},
        {"n": 2, "name": "Envelope"},
        {"n": 3, "name": "Pipe"},
        {"n": 26, "name": "Butterfly"},
    ]


def test_parse_name_list_warns_dup_gap_and_ignored() -> None:
    res = svc.parse_name_list("1. A\n1. B\njunk line\n3. C")
    names = {it["n"]: it["name"] for it in res["items"]}
    assert names == {1: "A", 3: "C"}  # first "1" wins, dup dropped
    joined = " ".join(res["warnings"])
    assert "duplicate number 1" in joined
    assert "ignored line" in joined
    assert "missing numbers: [2]" in joined


def test_parse_name_list_flags_duplicate_names() -> None:
    res = svc.parse_name_list("1. Scarf\n2. Scarf")
    assert any("duplicate name" in w for w in res["warnings"])


# ── join_markers_to_names ────────────────────────────────────────────────────


def test_join_markers_to_names() -> None:
    markers = [
        {"value": 1, "xPct": 10.0, "yPct": 20.0, "conf": 0.9},
        {"value": 2, "xPct": 30.0, "yPct": 40.0, "conf": 0.8},
    ]
    names = [{"n": 1, "name": "A"}, {"n": 3, "name": "C"}]
    points, unmatched_numbers, unmatched_names = svc.join_markers_to_names(markers, names)
    assert points == [{"n": 1, "name": "A", "xPct": 10.0, "yPct": 20.0}]
    assert unmatched_numbers == [2]  # marker with no name
    assert unmatched_names == [3]  # name with no marker


# ── save_scene round-trip (DB-backed) ────────────────────────────────────────


@pytest.fixture
def scene_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolated empty DB; skip the one-time legacy map.yaml import so the repo's
    # real scenes don't leak into these assertions.
    monkeypatch.setattr(dreamscape_db, "_LEGACY_MAP_REL", "does/not/exist/map.yaml")
    dreamscape_db.set_db_path_for_tests(tmp_path / "scenes.db")
    yield
    dreamscape_db.set_db_path_for_tests(None)


@pytest.mark.usefixtures("scene_db")
def test_save_scene_roundtrip_and_activate() -> None:
    res = svc.save_scene(
        "yard",
        title="Yard",
        source_image="games/wos/events/dreamscape_memory/references/maps/yard.png",
        scene_rect={"left": 2.0, "top": 8.0, "width": 96.0, "height": 70.0},
        points=[
            {"n": 1, "name": "Parachutte", "xPct": 12.3, "yPct": 45.6},
            {"n": 26, "name": "Butterfly", "xPct": 71.2, "yPct": 33.0},
        ],
        activate=True,
    )
    assert res == {"ok": True, "slug": "yard", "point_count": 2, "active": "yard"}

    scene = svc.get_scene("yard")
    assert scene["title"] == "Yard"
    assert scene["active"] is True
    assert scene["scene_rect"] == {"left": 2.0, "top": 8.0, "width": 96.0, "height": 70.0}
    assert scene["points"] == [
        {"n": 1, "name": "Parachutte", "xPct": 12.3, "yPct": 45.6},
        {"n": 26, "name": "Butterfly", "xPct": 71.2, "yPct": 33.0},
    ]


@pytest.mark.usefixtures("scene_db")
def test_save_scene_second_scene_keeps_active() -> None:
    svc.save_scene(
        "yard", title="Yard", source_image="a.png", scene_rect=None,
        points=[{"n": 1, "name": "Cat", "xPct": 1.0, "yPct": 2.0}], activate=True,
    )
    svc.save_scene(
        "garden", title="Garden", source_image="b.png", scene_rect=None,
        points=[{"n": 1, "name": "Map", "xPct": 3.0, "yPct": 4.0}], activate=False,
    )
    listed = svc.list_scenes()
    assert {s["slug"] for s in listed["scenes"]} == {"yard", "garden"}
    assert listed["active"] == "yard"  # second save (activate=False) didn't steal it


@pytest.mark.usefixtures("scene_db")
def test_save_scene_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate item name"):
        svc.save_scene(
            "yard", title="Yard", source_image="a.png", scene_rect=None,
            points=[
                {"n": 1, "name": "Scarf", "xPct": 1.0, "yPct": 2.0},
                {"n": 2, "name": "Scarf", "xPct": 3.0, "yPct": 4.0},
            ],
            activate=False,
        )


@pytest.mark.usefixtures("scene_db")
def test_list_and_get_scene() -> None:
    svc.save_scene(
        "yard", title="Yard", source_image="a.png",
        scene_rect={"left": 0.0, "top": 0.0, "width": 100.0, "height": 100.0},
        points=[{"n": 1, "name": "Cat", "xPct": 1.0, "yPct": 2.0}], activate=True,
    )
    listed = svc.list_scenes()
    assert listed["active"] == "yard"
    yard = next(s for s in listed["scenes"] if s["slug"] == "yard")
    assert yard["point_count"] == 1

    detail = svc.get_scene("yard")
    assert detail["active"] is True
    assert detail["points"] == [{"n": 1, "name": "Cat", "xPct": 1.0, "yPct": 2.0}]
    with pytest.raises(FileNotFoundError):
        svc.get_scene("missing")
