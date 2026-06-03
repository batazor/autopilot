"""Unit tests for the Dreamscape Memory solver's pure logic.

The handler's IO (Redis reads, taps) is thin; the logic worth protecting is
point parsing, guide->frame mapping, word normalization, fuzzy recovery, and
percent->pixel tap resolution. ``_load_targets`` now sources the active scene
from the module DB (``config.dreamscape_db``).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from config import dreamscape_db

MODULE_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "dreamscape_memory_exec", MODULE_DIR / "exec.py"
)
assert _spec and _spec.loader
solve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(solve)


def test_normalize_word_collapses_case_and_whitespace() -> None:
    assert solve._normalize_word("  Book ") == "book"
    assert solve._normalize_word("Camp\tFire") == "camp fire"
    assert solve._normalize_word(None) == ""


# ── _points_to_targets (pure) ─────────────────────────────────────────────────


def test_points_to_targets_parses_and_skips_malformed() -> None:
    points = [
        {"n": 1, "name": "Book", "xPct": 48.5, "yPct": 41.0},
        {"n": 2, "name": "WOLF", "xPct": 44, "yPct": 55.5},
        {"n": 3, "name": "broken", "xPct": 10},  # missing yPct -> skipped
        "nope",  # not a dict -> skipped
        {"n": 4, "name": "", "xPct": 1, "yPct": 2},  # empty name -> skipped
    ]
    assert solve._points_to_targets(points) == {
        "book": (48.5, 41.0),
        "wolf": (44.0, 55.5),
    }


def test_points_to_targets_maps_guide_to_frame_via_scene_rect() -> None:
    points = [{"n": 1, "name": "Cat", "xPct": 50.0, "yPct": 50.0}]
    # frame = origin + guide/100 * size: x = 10 + 0.5*50 = 35, y = 20 + 0.5*40 = 40
    assert solve._points_to_targets(points, (10.0, 20.0, 50.0, 40.0)) == {
        "cat": (35.0, 40.0)
    }


def test_points_to_targets_non_list_is_empty() -> None:
    assert solve._points_to_targets(None) == {}
    assert solve._points_to_targets({"name": "Cat"}) == {}


# ── _load_targets (DB-backed) ─────────────────────────────────────────────────


@pytest.fixture
def scene_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolated empty DB; skip the one-time legacy map.yaml import so the repo's
    # real scenes don't leak into these assertions.
    monkeypatch.setattr(dreamscape_db, "_LEGACY_MAP_REL", "does/not/exist/map.yaml")
    dreamscape_db.set_db_path_for_tests(tmp_path / "scenes.db")
    yield
    dreamscape_db.set_db_path_for_tests(None)


@pytest.mark.usefixtures("scene_db")
def test_load_targets_picks_active_scene() -> None:
    # "Butterfly" lives in both scenes at different spots; only the active wins.
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 26, "name": "Butterfly", "xPct": 50.0, "yPct": 40.0}],
        activate=True,
    )
    dreamscape_db.upsert_scene(
        "garden", title="Garden", source_image="", scene_rect=None,
        points=[{"n": 9, "name": "Butterfly", "xPct": 10.0, "yPct": 10.0}],
        activate=False,
    )
    assert solve._load_targets() == {"butterfly": (50.0, 40.0)}


@pytest.mark.usefixtures("scene_db")
def test_load_targets_applies_scene_rect() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="",
        scene_rect={"left": 10.0, "top": 20.0, "width": 50.0, "height": 40.0},
        points=[{"n": 1, "name": "Cat", "xPct": 50.0, "yPct": 50.0}],
        activate=True,
    )
    assert solve._load_targets() == {"cat": (35.0, 40.0)}


@pytest.mark.usefixtures("scene_db")
def test_load_targets_no_active_is_noop() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Cat", "xPct": 1.0, "yPct": 2.0}],
        activate=False,
    )
    assert solve._load_targets() == {}


# ── _resolve_taps ─────────────────────────────────────────────────────────────


def test_resolve_taps_maps_words_to_pixels_and_reports_misses() -> None:
    targets = {"book": (50.0, 40.0), "smoke": (52.0, 30.0)}
    hits, misses = solve._resolve_taps(
        ["Book", "Wolf", "  smoke", ""], targets, 720, 1280, fuzz_threshold=0
    )
    assert [(w, (p.x, p.y)) for w, p in hits] == [
        ("Book", (360, 512)),
        ("  smoke", (374, 384)),
    ]
    assert misses == ["Wolf"]


def test_resolve_taps_fuzzy_recovers_ocr_typos() -> None:
    targets = {"lightning": (50.0, 40.0), "snowman": (10.0, 20.0)}
    # OCR garbles a character; fuzzy matching taps the intended item anyway.
    hits, misses = solve._resolve_taps(["Lightening", "Snowmann"], targets, 720, 1280)
    assert [(w, (p.x, p.y)) for w, p in hits] == [
        ("Lightening", (360, 512)),
        ("Snowmann", (72, 256)),
    ]
    assert misses == []


def test_resolve_taps_fuzzy_threshold_zero_disables() -> None:
    targets = {"lightning": (50.0, 40.0)}
    hits, misses = solve._resolve_taps(
        ["Lightening"], targets, 720, 1280, fuzz_threshold=0
    )
    assert hits == []
    assert misses == ["Lightening"]


def test_resolve_taps_fuzzy_keeps_near_collisions_apart() -> None:
    # "Cart" vs "Cat" sit below the default cutoff, so an unmapped word that is
    # merely similar to a real item is still reported as a miss, not mis-tapped.
    targets = {"cart": (50.0, 40.0)}
    hits, misses = solve._resolve_taps(["Cat"], targets, 720, 1280)
    assert hits == []
    assert misses == ["Cat"]


# ── reference-sample scene (committed fixture, real 720x1280 ground truth) ─────
#
# A real recall-road scene hand-mapped in the legacy bot and ported here as a
# committed fixture (+ screenshot). Points are full-frame percentages, so the
# percent->pixel round-trip must recover the original device taps exactly. This
# is the end-to-end regression guard over the DB → _load_targets → tap pipeline.

_FIXTURE = MODULE_DIR / "tests" / "fixtures" / "reference_sample_scene.json"


def _activate_reference_scene() -> dict:
    scene = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    dreamscape_db.upsert_scene(
        scene["slug"],
        title=scene["title"],
        source_image=scene["source_image"],
        scene_rect=scene["scene_rect"],
        points=scene["points"],
        activate=True,
    )
    return scene


@pytest.mark.usefixtures("scene_db")
def test_reference_sample_round_trips_to_known_device_pixels() -> None:
    _activate_reference_scene()
    targets = solve._load_targets()
    assert len(targets) == 34
    # Verified taps from the original hand-mapped 720x1280 screenshot.
    hits, misses = solve._resolve_taps(
        ["Lightning", "Snowman", "Watering Can", "Pruning Shears"], targets, 720, 1280
    )
    assert [(w, (p.x, p.y)) for w, p in hits] == [
        ("Lightning", (269, 278)),
        ("Snowman", (136, 758)),
        ("Watering Can", (482, 846)),
        ("Pruning Shears", (298, 666)),
    ]
    assert misses == []


@pytest.mark.usefixtures("scene_db")
def test_reference_sample_fuzzy_recovers_garbled_ocr() -> None:
    _activate_reference_scene()
    targets = solve._load_targets()
    # OCR garbles of real items in this scene still tap the right spot.
    hits, misses = solve._resolve_taps(["Snowmann", "Wateing Can"], targets, 720, 1280)
    assert [(w, (p.x, p.y)) for w, p in hits] == [
        ("Snowmann", (136, 758)),
        ("Wateing Can", (482, 846)),
    ]
    assert misses == []
