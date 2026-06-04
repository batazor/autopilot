"""Unit tests for the Dreamscape Memory solver's pure logic.

The handler's IO (Redis reads, taps) is thin; the logic worth protecting is
point parsing, guide->frame mapping, word normalization, fuzzy recovery, and
percent->pixel tap resolution. ``_load_targets`` now sources the active scene
from the module DB (``config.dreamscape_db``).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import cv2  # type: ignore[import-untyped]
import numpy as np
import pytest
import yaml

from config import dreamscape_db
from config.loader import get_settings
from ocr.client import OcrClient, OCRResult

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


def test_normalize_level_name_strips_ocr_separators_and_progress() -> None:
    assert solve._normalize_level_name("Practice|Level · 23%") == "practice level"
    assert solve._normalize_level_name("Practice Level 23%") == "practice level"


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


# ── scene matching by on-screen level name ────────────────────────────────────

_SCENES = [
    {"slug": "aquarium", "title": "Aquarium", "season": 1},
    {"slug": "aquarium-s3", "title": "Aquarium (S3)", "season": 3},
    {"slug": "museum-s3", "title": "Museum (S3)", "season": 3},
    {"slug": "monument", "title": "Monument", "season": 100},
    {"slug": "practice-level", "title": "Practice Level", "season": 0},
]


def test_scene_base_name_strips_season_tags() -> None:
    assert solve._scene_base_name("Aquarium (S3)", "aquarium-s3") == "Aquarium"
    assert solve._scene_base_name("", "garden-s2") == "garden"
    assert solve._scene_base_name("", "monument") == "monument"


def test_match_scene_prefers_live_season_on_name_collision() -> None:
    # "Aquarium" exists in S1 and S3 with different layouts; the live (active)
    # season decides which one the level name resolves to.
    assert solve._match_scene_slug("Aquarium", _SCENES, prefer_season=3) == "aquarium-s3"
    assert solve._match_scene_slug("Aquarium", _SCENES, prefer_season=1) == "aquarium"


def test_match_scene_defaults_to_highest_season() -> None:
    assert solve._match_scene_slug("Aquarium", _SCENES) == "aquarium-s3"


def test_match_scene_fuzzy_recovers_ocr_typo() -> None:
    assert solve._match_scene_slug("Aquaium", _SCENES, prefer_season=3) == "aquarium-s3"


def test_match_scene_handles_title_ocr_progress_noise() -> None:
    assert solve._match_scene_slug("Practice|Level · 23%", _SCENES) == "practice-level"


def test_match_scene_no_match_is_none() -> None:
    assert solve._match_scene_slug("Spaceport", _SCENES) is None
    assert solve._match_scene_slug("", _SCENES) is None


def _dreamscape_region_def(name: str) -> dict:
    area_doc = yaml.safe_load((MODULE_DIR / "area.yaml").read_text(encoding="utf-8"))
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == name:
                return region
    msg = f"region not found: {name}"
    raise AssertionError(msg)


def test_practice_level_aurora_title_ocr_with_enhance_line() -> None:
    settings = get_settings()
    tesseract_cmd = str(settings.ocr.tesseract_cmd or "tesseract")
    if shutil.which(tesseract_cmd) is None and not Path(tesseract_cmd).exists():
        pytest.skip(f"tesseract executable not found: {tesseract_cmd!r}")

    image_path = (
        MODULE_DIR
        / "references"
        / "maps"
        / "practice-level"
        / "practice-level-aurora.png"
    )
    image = cv2.imread(str(image_path))
    assert image is not None, f"failed to read {image_path}"

    region = _dreamscape_region_def("dreamscape_memory.level.name")
    assert region.get("preprocess") == "enhance_line"
    bbox = region["bbox"]
    frame_h, frame_w = image.shape[:2]
    x = int(round(float(bbox["x"]) / 100.0 * frame_w))
    y = int(round(float(bbox["y"]) / 100.0 * frame_h))
    w = int(round(float(bbox["width"]) / 100.0 * frame_w))
    h = int(round(float(bbox["height"]) / 100.0 * frame_h))

    text, confidence = OcrClient(settings)._run_tesseract(
        image[y : y + h, x : x + w],
        preprocess="enhance_line",
    )

    assert text == "Practice Level"
    assert confidence >= 0.9


@pytest.mark.usefixtures("scene_db")
def test_select_scene_matches_level_within_active_season() -> None:
    # Active scene marks Season 3 as live; the OCR'd level name picks the room,
    # not the same-named Season 1 scene (different coordinates).
    dreamscape_db.upsert_scene(
        "museum-s3", title="Museum (S3)", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "X", "xPct": 1.0, "yPct": 1.0}],
        activate=True, season=3,
    )
    dreamscape_db.upsert_scene(
        "aquarium", title="Aquarium", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Shark", "xPct": 10.0, "yPct": 10.0}],
        activate=False, season=1,
    )
    dreamscape_db.upsert_scene(
        "aquarium-s3", title="Aquarium (S3)", source_image="", scene_rect=None,
        points=[{"n": 2, "name": "Shark", "xPct": 80.0, "yPct": 80.0}],
        activate=False, season=3,
    )
    scene = solve._select_scene("Aquarium", solve._DEFAULT_FUZZ_THRESHOLD)
    assert scene is not None and scene["slug"] == "aquarium-s3"
    assert solve._targets_for_scene(scene) == {"shark": (80.0, 80.0)}


@pytest.mark.usefixtures("scene_db")
def test_select_scene_falls_back_to_active_without_level_name() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Cat", "xPct": 5.0, "yPct": 6.0}],
        activate=True,
    )
    scene = solve._select_scene("", solve._DEFAULT_FUZZ_THRESHOLD)
    assert scene is not None and scene["slug"] == "yard"


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


class _FakeDreamscapeActions:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []
        self.frame = np.zeros((1280, 720, 3), dtype=np.uint8)

    def screen_resolution(self, _instance_id: str) -> tuple[int, int]:
        return (720, 1280)

    def capture_screen_bgr_cached(self, _instance_id: str, *, max_age_ms: float) -> np.ndarray:
        assert max_age_ms > 0
        return self.frame

    def tap(self, _instance_id: str, point: object) -> bool:
        self.taps.append((point.x, point.y))
        return True


class _FakeDreamscapeOcr:
    async def ocr_regions(
        self,
        _image: np.ndarray,
        _regions: list[object],
        *,
        region_ids: list[str] | None = None,
        region_preprocess: list[str | None] | None = None,
    ) -> list[OCRResult]:
        assert region_preprocess == ["enhance_line", None, None]
        values = {
            "dreamscape_memory.level.name": "Practice Level",
            "dreamscape_memory.1": "Book",
            "dreamscape_memory.2": "Smoke",
        }
        return [
            OCRResult(region_id=rid, text=values.get(rid, ""), confidence=1.0)
            for rid in (region_ids or [])
        ]


def _minimal_solver_area_doc() -> dict:
    def reg(name: str, *, preprocess: str | None = None) -> dict:
        out = {
            "name": name,
            "action": "text",
            "threshold": 0.8,
            "bbox": {
                "x": 0,
                "y": 0,
                "width": 10,
                "height": 5,
                "original_width": 720,
                "original_height": 1280,
            },
            "type": "string",
        }
        if preprocess:
            out["preprocess"] = preprocess
            out["threshold"] = 0.9
        return out

    return {
        "version": 2,
        "screens": [
            {
                "screen_id": "",
                "regions": [
                    reg("dreamscape_memory.level.name", preprocess="enhance_line"),
                    reg("dreamscape_memory.1"),
                    reg("dreamscape_memory.2"),
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_solve_loop_remembers_clicked_words(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = _FakeDreamscapeActions()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: _FakeDreamscapeOcr())
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(
        solve,
        "_select_scene",
        lambda _level_name, _fuzz_threshold: {
            "slug": "practice-level",
            "scene_rect": None,
            "points": [
                {"name": "Book", "xPct": 50.0, "yPct": 40.0},
                {"name": "Smoke", "xPct": 52.0, "yPct": 30.0},
            ],
        },
    )

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {
                "regions": ["dreamscape_memory.1", "dreamscape_memory.2"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["seen"] == ["Book", "Smoke"]
    assert ctx.result["clicked_keys"] == ["book", "smoke"]
    assert ctx.result["skipped_clicked"] == ["Book", "Smoke"]


@pytest.mark.asyncio
async def test_solve_loop_stops_on_all_items_found(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = _FakeDreamscapeActions()
    async def detect_terminal(_image, hint=None):
        return solve._TERMINAL_ALL_FOUND

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {
                "regions": ["dreamscape_memory.1", "dreamscape_memory.2"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == []
    assert ctx.result["iterations"] == 1
    assert ctx.result["terminal_screen"] == solve._TERMINAL_ALL_FOUND
    assert ctx.result["status"] == "won"


@pytest.mark.asyncio
async def test_solve_loop_treats_start_screen_after_tap_as_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    detect_calls = 0

    async def detect_terminal(_image, hint=None):
        nonlocal detect_calls
        detect_calls += 1
        return solve._START_SCREEN

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: _FakeDreamscapeOcr())
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(
        solve,
        "_select_scene",
        lambda _level_name, _fuzz_threshold: {
            "slug": "practice-level",
            "scene_rect": None,
            "points": [{"name": "Book", "xPct": 50.0, "yPct": 40.0}],
        },
    )

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {
                "regions": ["dreamscape_memory.1", "dreamscape_memory.2"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert detect_calls == 2
    assert actions.taps == [(360, 512)]
    assert ctx.result["terminal_screen"] == solve._START_SCREEN
    assert ctx.result["status"] == "won"


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
