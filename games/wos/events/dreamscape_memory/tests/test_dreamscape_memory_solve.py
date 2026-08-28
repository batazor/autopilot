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
    assert solve._normalize_word("PocketWatch") == "pocket watch"
    assert solve._normalize_word(None) == ""


def test_normalize_level_name_strips_ocr_separators_and_progress() -> None:
    assert solve._normalize_level_name("Practice|Level · 23%") == "practice level"
    assert solve._normalize_level_name("Practice Level 23%") == "practice level"


def test_parse_help_counter_reads_digits_or_none() -> None:
    assert solve._parse_help_counter("2") == 2
    assert solve._parse_help_counter("x2") == 2
    assert solve._parse_help_counter("") is None


def test_actionable_unmapped_word_requires_three_letters() -> None:
    assert solve._is_actionable_unmapped_word("Se") is False
    assert solve._is_actionable_unmapped_word("H2O") is False
    assert solve._is_actionable_unmapped_word("Axe") is True


def test_actionable_unmapped_word_rejects_ocr_garbage() -> None:
    # Noise from OCR of an unsettled/animating slot must not reach helper-learn.
    assert solve._is_actionable_unmapped_word("ooceeeeenne EEEEEEEEEREET") is False
    assert solve._is_actionable_unmapped_word("eeeeeeee") is False  # 4+ same-char run
    assert solve._is_actionable_unmapped_word("xkqrtwn") is False  # no vowels
    # Real item words (including plausibly OCR-garbled ones) still pass.
    for word in ("Snowman", "Grilled Fish", "Clay Jug", "Aurora", "Lightning", "Snowmann"):
        assert solve._is_actionable_unmapped_word(word) is True, word


def test_read_word_floor_keeps_two_letter_items_drops_single_letters() -> None:
    # The read-level floor must stay below the catalog's shortest real words
    # («Ёж», «Як») while still dropping the one-letter shreds a degraded live
    # frame produces ('ь' for «Мышь» fuzzy-ties half the vocabulary).
    from ocr.word_cleaning import is_plausible_word_text

    floor = solve._MIN_READ_WORD_LETTERS
    assert is_plausible_word_text("Ёж", min_letters=floor)
    assert is_plausible_word_text("Як", min_letters=floor)
    assert not is_plausible_word_text("ь", min_letters=floor)
    assert not is_plausible_word_text("ш", min_letters=floor)


# ── _is_pill_crop (dialog-overlay read gate) ─────────────────────────────────


def _pill_like_crop(
    fill_bgr: tuple[int, int, int],
    text_bgr: tuple[int, int, int] = (40, 30, 30),
) -> np.ndarray:
    """A slot-sized crop: solid fill with a darker word stripe through it."""
    crop = np.full((38, 200, 3), fill_bgr, dtype=np.uint8)
    crop[14:26, 60:140] = text_bgr
    return crop


def test_is_pill_crop_accepts_active_and_struck_fills() -> None:
    assert solve._is_pill_crop(_pill_like_crop((224, 183, 178))) is True  # active
    assert solve._is_pill_crop(_pill_like_crop((207, 147, 132))) is True  # struck


def test_is_pill_crop_rejects_dialog_shade_and_bad_input() -> None:
    # Story/ending dialogs cover the slot band: dark shade or a pale card, both
    # far from the two pill fills — the read must not happen at all.
    assert solve._is_pill_crop(_pill_like_crop((70, 50, 80))) is False
    assert solve._is_pill_crop(_pill_like_crop((245, 240, 245))) is False
    assert solve._is_pill_crop(None) is False


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


def test_match_scene_resolves_via_alt_title() -> None:
    # A scene whose in-game level name differs from its title resolves through
    # the operator-supplied alternate name (and still by its own title).
    scenes = [{"slug": "yard", "title": "Yard", "alt_title": "Backyard", "season": 1}]
    assert solve._match_scene_slug("Backyard", scenes) == "yard"
    assert solve._match_scene_slug("Yard", scenes) == "yard"
    # Fuzzy recovery works against the alias too.
    assert solve._match_scene_slug("Backyrd", scenes) == "yard"


def test_match_scene_resolves_via_any_alt_title_in_list() -> None:
    # A scene can carry several aliases; the level name resolves through any of
    # them (and still by its own title).
    scenes = [
        {
            "slug": "yard",
            "title": "Yard",
            "alt_titles": ["Backyard", "Patio"],
            "season": 1,
        }
    ]
    assert solve._match_scene_slug("Yard", scenes) == "yard"
    assert solve._match_scene_slug("Backyard", scenes) == "yard"
    assert solve._match_scene_slug("Patio", scenes) == "yard"
    assert solve._match_scene_slug("Spaceport", scenes) is None


def _dreamscape_region_def(name: str) -> dict:
    area_doc = yaml.safe_load((MODULE_DIR / "area.yaml").read_text(encoding="utf-8"))
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == name:
                return region
    msg = f"region not found: {name}"
    raise AssertionError(msg)


def test_practice_level_aurora_title_ocr_with_title_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # EN reference screenshot — pin the capture language (see the word test).
    monkeypatch.setattr(OcrClient, "_resolve_lang", lambda _self: "eng")
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
    assert region.get("preprocess") == "title_line"
    bbox = region["bbox"]
    frame_h, frame_w = image.shape[:2]
    x = int(round(float(bbox["x"]) / 100.0 * frame_w))
    y = int(round(float(bbox["y"]) / 100.0 * frame_h))
    w = int(round(float(bbox["width"]) / 100.0 * frame_w))
    h = int(round(float(bbox["height"]) / 100.0 * frame_h))

    text, confidence = OcrClient(settings)._run_tesseract(
        image[y : y + h, x : x + w],
        preprocess="title_line",
    )

    assert text == "Practice Level"
    assert confidence >= 0.9


def test_practice_level_word_ocr_with_word_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The EN reference screenshot reads as garbage under an operator's
    # WOS_OCR_LANG=rus — the fixture pins the language it was captured in.
    monkeypatch.setattr(OcrClient, "_resolve_lang", lambda _self: "eng")
    settings = get_settings()
    tesseract_cmd = str(settings.ocr.tesseract_cmd or "tesseract")
    if shutil.which(tesseract_cmd) is None and not Path(tesseract_cmd).exists():
        pytest.skip(f"tesseract executable not found: {tesseract_cmd!r}")

    image_path = MODULE_DIR / "references" / "practice_level.png"
    image = cv2.imread(str(image_path))
    assert image is not None, f"failed to read {image_path}"

    frame_h, frame_w = image.shape[:2]
    expected = {
        "dreamscape_memory.1": "Book",
        "dreamscape_memory.2": "Wolf",
        "dreamscape_memory.3": "Smoke",
    }
    client = OcrClient(settings)
    for name, expected_text in expected.items():
        region = _dreamscape_region_def(name)
        assert region.get("preprocess") == "word_line"
        bbox = region["bbox"]
        x = int(round(float(bbox["x"]) / 100.0 * frame_w))
        y = int(round(float(bbox["y"]) / 100.0 * frame_h))
        w = int(round(float(bbox["width"]) / 100.0 * frame_w))
        h = int(round(float(bbox["height"]) / 100.0 * frame_h))

        text, confidence = client._run_tesseract(
            image[y : y + h, x : x + w],
            preprocess="word_line",
        )

        assert text == expected_text
        assert confidence >= 0.9


@pytest.mark.usefixtures("scene_db")
def test_select_scene_detects_by_words_within_active_season() -> None:
    # Active scene marks Season 3 as live. "Shark" lives in both the S1 and S3
    # Aquarium (different coordinates); detection by the on-screen word resolves
    # to the live season, not the same-named Season 1 scene.
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
    scene = solve._select_scene(["Shark"], solve._DEFAULT_FUZZ_THRESHOLD)
    assert scene is not None and scene["slug"] == "aquarium-s3"
    assert solve._targets_for_scene(scene) == {"shark": (80.0, 80.0)}


@pytest.mark.usefixtures("scene_db")
def test_select_scene_falls_back_to_active_without_words() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Cat", "xPct": 5.0, "yPct": 6.0}],
        activate=True,
    )
    scene = solve._select_scene([], solve._DEFAULT_FUZZ_THRESHOLD)
    assert scene is not None and scene["slug"] == "yard"


def test_match_scene_by_words_relaxes_overlap_three_then_two_then_one() -> None:
    # The detector demands the strongest overlap first, relaxing only when nothing
    # matches: all three words → two → one.
    scenes = [
        {"slug": "kitchen", "season": 1, "names": ["Apple", "Bread", "Cup"]},
        {"slug": "garden", "season": 1, "names": ["Apple", "Rose"]},
        {"slug": "study", "season": 1, "names": ["Pen"]},
    ]
    # All three present → the scene holding all three.
    assert solve._match_scene_by_words(["Apple", "Bread", "Cup"], scenes) == "kitchen"
    # No scene holds all three; "Apple"+"Rose" overlap (2) beats kitchen's 1.
    assert solve._match_scene_by_words(["Apple", "Rose", "Zebra"], scenes) == "garden"
    # Only one word lands anywhere.
    assert solve._match_scene_by_words(["Pen", "Zebra", "Yak"], scenes) == "study"
    # Nothing matches.
    assert solve._match_scene_by_words(["Zebra", "Yak"], scenes) is None


def test_match_scene_by_words_drops_ocr_garbage_before_counting() -> None:
    # Garbage reads (too short / noise) are ignored, so a junk slot lowers the bar
    # instead of mis-matching.
    scenes = [{"slug": "kitchen", "season": 1, "names": ["Apple", "Bread"]}]
    assert solve._match_scene_by_words(["Apple", "oe", "iin"], scenes) == "kitchen"


def test_match_scene_by_words_accepts_short_exact_known_word() -> None:
    scenes = [{"slug": "attic-s3", "season": 3, "names": ["X"]}]
    assert solve._match_scene_by_words(["X"], scenes, prefer_season=3) == "attic-s3"


def test_match_scene_by_words_breaks_season_ties_toward_prefer() -> None:
    scenes = [
        {"slug": "aquarium", "season": 1, "names": ["Shark"]},
        {"slug": "aquarium-s3", "season": 3, "names": ["Shark"]},
    ]
    assert solve._match_scene_by_words(["Shark"], scenes, prefer_season=3) == "aquarium-s3"
    assert solve._match_scene_by_words(["Shark"], scenes, prefer_season=1) == "aquarium"


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


def test_resolve_taps_maps_single_letter_dictionary_word() -> None:
    targets = {"x": (25.0, 75.0)}
    hits, misses = solve._resolve_taps(["X"], targets, 720, 1280, fuzz_threshold=0)
    assert [(w, (p.x, p.y)) for w, p in hits] == [("X", (180, 960))]
    assert misses == []


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


def test_resolve_taps_fuzzy_skips_ambiguous_grilled_prefix() -> None:
    targets = {
        "grilled skewer": (47.66, 35.92),
        "grilled fish": (63.61, 43.36),
    }

    hits, misses = solve._resolve_taps(
        ["Grilled", "Grilled S", "Grilled Ske", "Grilled Fish"],
        targets,
        720,
        1280,
    )

    assert [(w, (p.x, p.y)) for w, p in hits] == [
        ("Grilled Ske", (343, 460)),
        ("Grilled Fish", (458, 555)),
    ]
    assert misses == []


def test_point_to_scene_percent_reverses_scene_rect() -> None:
    xy = solve._point_to_scene_percent(
        solve.Point(360, 512),
        720,
        1280,
        {"left": 10.0, "top": 20.0, "width": 50.0, "height": 40.0},
    )
    assert xy == (80.0, 50.0)


def test_detect_help_highlight_motion_from_two_frames() -> None:
    before = np.zeros((1280, 720, 3), dtype=np.uint8)
    after = before.copy()
    cv2.circle(after, (325, 627), 44, (0, 180, 255), 10)
    cv2.circle(after, (325, 627), 58, (80, 120, 255), 6)

    point = solve._detect_help_highlight_motion(before, after)

    assert point is not None
    assert abs(point.x - 325) <= 5
    assert abs(point.y - 627) <= 5


def test_detect_help_highlight_motion_multi_uses_repeated_candidate() -> None:
    before = np.zeros((1280, 720, 3), dtype=np.uint8)
    false_once = before.copy()
    cv2.circle(false_once, (120, 220), 48, (0, 180, 255), 10)
    hint_1 = before.copy()
    hint_2 = before.copy()
    hint_3 = before.copy()
    for frame in (hint_1, hint_2, hint_3):
        cv2.circle(frame, (325, 627), 44, (0, 180, 255), 10)
        cv2.circle(frame, (325, 627), 58, (80, 120, 255), 6)

    point = solve._detect_help_highlight_motion_multi(
        [before, false_once, hint_1, hint_2, hint_3]
    )

    assert point is not None
    assert abs(point.x - 325) <= 5
    assert abs(point.y - 627) <= 5


def test_word_region_visual_found_detects_struck_pill_only() -> None:
    # Real pill fills (medians from live 720x1280 frames): pale-lavender active
    # vs slate struck. The classifier is nearest-centroid over the background —
    # the text colour must not matter.
    active = np.full((54, 290, 3), (224, 183, 178), dtype=np.uint8)
    cv2.putText(
        active, "Scrolls", (70, 36), cv2.FONT_HERSHEY_SIMPLEX,
        1.0, (250, 250, 255), 2, cv2.LINE_AA,
    )
    found = np.full((54, 290, 3), (207, 147, 132), dtype=np.uint8)
    cv2.putText(
        found, "Pouch", (82, 36), cv2.FONT_HERSHEY_SIMPLEX,
        1.0, (55, 58, 70), 2, cv2.LINE_AA,
    )
    cv2.line(found, (85, 28), (205, 28), (45, 48, 65), 4, cv2.LINE_AA)

    assert solve._is_word_region_visually_found(active) is False
    assert solve._is_word_region_visually_found(found) is True


def test_word_region_visual_found_ignores_long_active_word() -> None:
    # The regression that shipped: a long dense word ("Морская звезда") on an
    # ACTIVE pill false-positived the old dark-text heuristic, locking the slot
    # as found so it was never tapped. Background classification must not care
    # how much text there is.
    active = np.full((54, 320, 3), (224, 183, 178), dtype=np.uint8)
    cv2.putText(
        active, "Morskaya zvezda", (10, 36), cv2.FONT_HERSHEY_SIMPLEX,
        0.9, (250, 250, 255), 2, cv2.LINE_AA,
    )
    assert solve._is_word_region_visually_found(active) is False


def test_word_region_visual_found_ignores_dark_or_blank_crop() -> None:
    # Non-pill content (the pre-round shade, a popup) is near NEITHER reference
    # colour — never "found".
    dark = np.full((54, 290, 3), 25, dtype=np.uint8)
    blank = np.full((54, 290, 3), 245, dtype=np.uint8)
    assert solve._is_word_region_visually_found(dark) is False
    assert solve._is_word_region_visually_found(blank) is False
def test_word_region_visual_found_uses_background_saturation_on_real_frame() -> None:
    # Real capture: slot 2 ("Smoke") is already solved/greyed; slots 1 and 3 are
    # active. The detector must flag only the desaturated found pill — the signal
    # the solver relies on to keep a found slot locked instead of re-clicking it.
    frame = cv2.imread(str(MODULE_DIR / "tests" / "fixtures" / "image_search.png"))
    assert frame is not None
    height, width = frame.shape[:2]
    pills = {
        "active_1": (5.9705, 89.7637, 28.4913, 2.9657),
        "found_2": (35.8694, 89.9899, 28.3370, 2.7595),
        "active_3": (66.2059, 89.8803, 28.0192, 2.9663),
    }
    found = {}
    for name, (x, y, bw, bh) in pills.items():
        px, py = int(round(x / 100 * width)), int(round(y / 100 * height))
        pw, ph = int(round(bw / 100 * width)), int(round(bh / 100 * height))
        found[name] = solve._is_word_region_visually_found(frame[py : py + ph, px : px + pw])
    assert found == {"active_1": False, "found_2": True, "active_3": False}


def test_word_region_visual_found_two_of_three_solved_on_real_frame() -> None:
    # Real capture (higher-res): slots 1 and 2 are already solved/greyed, slot 3
    # is still active. The colour detector works on percentage-placed pills
    # regardless of frame resolution, and must flag exactly the two found slots.
    frame = cv2.imread(str(MODULE_DIR / "tests" / "fixtures" / "find3.png"))
    assert frame is not None
    height, width = frame.shape[:2]
    pills = {
        "found_1": (5.9705, 89.7637, 28.4913, 2.9657),
        "found_2": (35.8694, 89.9899, 28.3370, 2.7595),
        "active_3": (66.2059, 89.8803, 28.0192, 2.9663),
    }
    found = {}
    for name, (x, y, bw, bh) in pills.items():
        px, py = int(round(x / 100 * width)), int(round(y / 100 * height))
        pw, ph = int(round(bw / 100 * width)), int(round(bh / 100 * height))
        found[name] = solve._is_word_region_visually_found(frame[py : py + ph, px : px + pw])
    assert found == {"found_1": True, "found_2": True, "active_3": False}


def _paint_pill_band(frame: np.ndarray) -> np.ndarray:
    """Make the word-slot band read as pills to the ``_is_pill_crop`` gate.

    The loop refuses to OCR a slot whose background is not a pill fill (that is
    how dialog overlays are kept out), so a harness frame must carry the active
    fill — with dark text-like stripes, or a uniform crop has no background
    median at all — wherever the slot regions sit. ``_minimal_solver_area_doc``
    parks every word slot at bbox (0, 0, 10%, 5%) → rows 0:64, cols 0:72.
    """
    band = frame[0:64, 0:72]
    band[:] = (224, 183, 178)
    band[::8] = (40, 30, 30)
    band[1::8] = (40, 30, 30)
    return frame


class _FakeDreamscapeActions:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []
        self.require_approval_values: list[bool] = []
        self.frame = _paint_pill_band(np.zeros((1280, 720, 3), dtype=np.uint8))

    def screen_resolution(self, _instance_id: str) -> tuple[int, int]:
        return (720, 1280)

    def capture_screen_bgr_cached(self, _instance_id: str, *, max_age_ms: float) -> np.ndarray:
        assert max_age_ms >= 0
        return self.frame

    def tap(
        self,
        _instance_id: str,
        point: object,
        *,
        require_approval: bool = True,
    ) -> bool:
        self.require_approval_values.append(require_approval)
        self.taps.append((point.x, point.y))
        return True


class _FakeDreamscapeOcr:
    def __init__(
        self,
        *,
        help_counter: str = "2",
        word_values_by_call: list[dict[str, str]] | None = None,
        confidence_by_call: list[dict[str, float]] | None = None,
    ) -> None:
        self.help_counter = help_counter
        self.region_id_calls: list[list[str]] = []
        self.word_values_by_call = word_values_by_call or [
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            }
        ]
        self.confidence_by_call = confidence_by_call or []

    async def ocr_regions(
        self,
        _image: np.ndarray,
        _regions: list[object],
        *,
        region_ids: list[str] | None = None,
        region_preprocess: list[str | None] | None = None,
    ) -> list[OCRResult]:
        ids = list(region_ids or [])
        # The two-line retry re-reads weak word slots in block mode; the fake
        # models single-line pills, so those calls return nothing and must not
        # consume the per-tick word queue below.
        if region_preprocess and all(p == "word_block" for p in region_preprocess):
            return [
                OCRResult(region_id=rid, text="", confidence=0.0) for rid in ids
            ]
        word_region_ids = {
            "dreamscape_memory.1",
            "dreamscape_memory.2",
            "dreamscape_memory.3",
        }
        reads_words = any(rid in word_region_ids for rid in ids)
        word_call_index = sum(
            1
            for call in self.region_id_calls
            if any(rid in word_region_ids for rid in call)
        )
        self.region_id_calls.append(ids)
        preprocess_by_region = {
            "dreamscape_memory.level.name": "title_line",
            "dreamscape_memory.1": "word_line",
            "dreamscape_memory.2": "word_line",
            "dreamscape_memory.3": "word_line",
            "dreamscape_memory.help.counter": "fast_digits",
        }
        assert region_preprocess == [preprocess_by_region[rid] for rid in ids]
        values = {
            "dreamscape_memory.level.name": "Practice Level",
            "dreamscape_memory.help.counter": self.help_counter,
        }
        if reads_words and word_call_index < len(self.word_values_by_call):
            values.update(self.word_values_by_call[word_call_index])
        confidences = (
            self.confidence_by_call[word_call_index]
            if reads_words and word_call_index < len(self.confidence_by_call)
            else {}
        )
        return [
            OCRResult(
                region_id=rid,
                text=values.get(rid, ""),
                confidence=confidences.get(rid, 1.0),
            )
            for rid in (region_ids or [])
        ]


class _FakeDreamscapeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hset(
        self,
        key: str,
        *args: object,
        mapping: dict[str, object] | None = None,
    ) -> int:
        row = self.hashes.setdefault(key, {})
        if mapping is not None:
            for field, value in mapping.items():
                row[str(field)] = str(value)
            return len(mapping)
        if len(args) != 2:
            msg = f"unexpected hset args: {args!r}"
            raise AssertionError(msg)
        row[str(args[0])] = str(args[1])
        return 1


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
                    reg("dreamscape_memory.level.name", preprocess="title_line"),
                    reg("dreamscape_memory.1", preprocess="word_line"),
                    reg("dreamscape_memory.2", preprocess="word_line"),
                    reg("dreamscape_memory.3", preprocess="word_line"),
                    {
                        "name": "dreamscape_memory.help.counter",
                        "action": "text",
                        "threshold": 0.5,
                        "preprocess": "fast_digits",
                        "type": "int",
                        "bbox": {
                            "x": 80,
                            "y": 75,
                            "width": 10,
                            "height": 5,
                            "original_width": 720,
                            "original_height": 1280,
                        },
                    },
                    {
                        "name": "dreamscape_memory.help",
                        "action": "exist",
                        "threshold": 0.9,
                        "bbox": {
                            "x": 80,
                            "y": 80,
                            "width": 10,
                            "height": 10,
                            "original_width": 720,
                            "original_height": 1280,
                        },
                    },
                ],
            }
        ],
    }


def _grey_when_point_tapped(
    actions: _FakeDreamscapeActions,
    point_region: dict[tuple[int, int], str],
):
    """A ``_found_word_regions_from_frame`` stand-in modelling a correct tap.

    A word slot greys out ("found") once the solver has tapped the scene point
    its word maps to — i.e. the background confirms the click on the next frame.
    ``point_region`` maps a tap point ``(x, y)`` to the word slot it solves. This
    is how a real capture confirms a tap; the solver only promotes a slot to
    ``clicked`` when this reports the slot greyed.
    """

    def detect(
        _image: object,
        _area_doc: dict[str, object],
        regions: list[str],
    ) -> set[str]:
        greyed = {point_region[p] for p in actions.taps if p in point_region}
        return greyed & set(regions)

    return detect


@pytest.mark.asyncio
async def test_solve_loop_remembers_clicked_words(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Both taps land: their pills grey on the next frame, confirming the clicks.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(
            actions,
            {(360, 512): "dreamscape_memory.1", (374, 384): "dreamscape_memory.2"},
        ),
    )
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

    # Each word tapped once (dispatch); the colour confirms them on iteration 2.
    assert actions.taps == [(360, 512), (374, 384)]
    assert actions.require_approval_values == [False, False]
    assert ctx.result["seen"] == ["Book", "Smoke"]
    assert ctx.result["clicked_keys"] == ["book", "smoke"]
    assert ctx.result["clicked_regions"] == ["dreamscape_memory.1", "dreamscape_memory.2"]
    # Colour confirmed both → settled. clicked is only ever set after confirmation.
    assert ctx.result["settled_regions"] == [
        "dreamscape_memory.1",
        "dreamscape_memory.2",
    ]
    assert ctx.result["pending_click_regions"] == []
    assert ctx.result["click_retries"] == []
    assert ctx.result["skipped_clicked"] == []
    assert ocr.region_id_calls == [
        # Scene detected from the word slots (no title OCR); the help counter is
        # then read on the same discovery tick.
        [
            "dreamscape_memory.1",
            "dreamscape_memory.2",
        ],
        [
            "dreamscape_memory.help.counter",
        ],
        # Both slots confirmed found on iteration 2 → only the helper counter
        # is still read.
        [
            "dreamscape_memory.help.counter",
        ],
    ]


@pytest.mark.asyncio
async def test_solve_loop_retaps_then_rejects_when_colour_never_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The tap is dispatched but the pill never greys (colour never confirms —
    # e.g. a wrong map coordinate). The solver re-taps after the confirm wait,
    # then gives up and surfaces the slot as 'rejected' — it is never recorded as
    # clicked, and it does not spin forever.
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Book"},
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Colour never confirms (pill stays active) → drives the re-tap/give-up path.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        lambda _image, _area_doc, _regions: set(),
    )
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
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "tap_confirm_wait": 1,
                "max_tap_attempts": 2,
                "max_iterations": 5,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # Dispatched once, re-tapped once (max_tap_attempts=2), then rejected.
    assert actions.taps == [(360, 512), (360, 512)]
    assert ctx.result["clicked"] == []
    assert ctx.result["clicked_regions"] == []
    assert ctx.result["settled_regions"] == []
    assert ctx.result["click_retries"] == [
        {
            "region": "dreamscape_memory.1",
            "word": "Book",
            "key": "book",
            "retry": 2,
        },
    ]
    assert ctx.result["click_retry_exhausted"] == [
        {
            "region": "dreamscape_memory.1",
            "word": "Book",
            "key": "book",
            "retries": 2,
        },
    ]
    assert ctx.result["slot_states"]["dreamscape_memory.1"]["fsm_status"] == "rejected"
    assert any(event["kind"] == "skip_rejected" for event in ctx.result["events"])


@pytest.mark.asyncio
async def test_solve_loop_clicks_new_word_in_same_slot_without_extra_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Smoke"},
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
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
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # Book is dispatched then, when the slot's word changes to Smoke before any
    # colour confirmation, the slot is reopened and Smoke is dispatched in the
    # same loop. Neither is colour-confirmed here, so nothing is recorded clicked;
    # Smoke's tap stays in flight (pending).
    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["clicked"] == []
    assert ctx.result["settled_regions"] == []
    assert ctx.result["pending_click_regions"] == ["dreamscape_memory.1"]


@pytest.mark.asyncio
async def test_solve_loop_skips_ocr_for_visually_found_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    redis = _FakeDreamscapeRedis()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.2": "Smoke",
            },
        ]
    )
    visual_calls = 0

    def visually_found(
        _image: object,
        _area_doc: dict[str, object],
        _names: list[str],
    ) -> set[str]:
        nonlocal visual_calls
        visual_calls += 1
        return {"dreamscape_memory.1"} if visual_calls >= 2 else set()

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_found_word_regions_from_frame", visually_found)
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
            self.redis_client = redis
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

    assert ocr.region_id_calls == [
        # Scene detected from the word slots (no title OCR), then the help counter
        # on the same discovery tick.
        [
            "dreamscape_memory.1",
            "dreamscape_memory.2",
        ],
        [
            "dreamscape_memory.help.counter",
        ],
        # Scene locked after the first match; slot 1 is visually found, so only
        # slot 2 and the help counter are read.
        [
            "dreamscape_memory.2",
            "dreamscape_memory.help.counter",
        ],
    ]
    assert "dreamscape_memory.1" in ctx.result["settled_regions"]
    assert ctx.result["region_words"] == {
        "dreamscape_memory.1": "Book",
        "dreamscape_memory.2": "Smoke",
    }
    live_state = json.loads(
        redis.hashes["wos:instance:bs1:state"]["dreamscape_memory.solve_state"]
    )
    assert live_state["settled_regions"] == ["dreamscape_memory.1"]
    # Only slot 1's tap was colour-confirmed → clicked. Slot 2's tap is still in
    # flight (its pill never greyed), so it is not recorded as clicked.
    assert live_state["clicked_regions"] == ["dreamscape_memory.1"]
    assert live_state["region_words"]["dreamscape_memory.1"] == "Book"
    assert live_state["slot_states"]["dreamscape_memory.1"]["status"] == "clicked"
    assert live_state["slot_states"]["dreamscape_memory.1"]["fsm_status"] == "clicked"
    assert live_state["slot_states"]["dreamscape_memory.1"]["word"] == "Book"


@pytest.mark.asyncio
async def test_solve_loop_treats_pre_greyed_slot_as_found_not_clicked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Slot 1 is already greyed when we arrive (solved earlier / by a teammate).
    # We never tapped it, so it is 'found', not 'clicked' — and we neither read
    # nor tap it. Only the still-active slot 2 is dispatched.
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
        ]
    )

    def visually_found(
        _image: object,
        _area_doc: dict[str, object],
        _names: list[str],
    ) -> set[str]:
        return {"dreamscape_memory.1"}

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_found_word_regions_from_frame", visually_found)
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
                "max_iterations": 1,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert ocr.region_id_calls == [
        # Slot 1 is already found (greyed) → skipped; the scene is detected from
        # slot 2 (the only readable word), then the help counter on the same tick.
        [
            "dreamscape_memory.2",
        ],
        [
            "dreamscape_memory.help.counter",
        ],
    ]
    assert actions.taps == [(374, 384)]
    assert ctx.result["clicked_regions"] == []
    assert ctx.result["settled_regions"] == ["dreamscape_memory.1"]
    assert ctx.result["slot_states"]["dreamscape_memory.1"]["fsm_status"] == "found"


@pytest.mark.asyncio
async def test_solve_loop_reopens_slot_when_next_word_wave_turns_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Smoke"},
        ]
    )
    visual_by_call = [
        set(),
        {"dreamscape_memory.1"},
        set(),
    ]
    visual_calls = 0

    def visually_found(
        _image: object,
        _area_doc: dict[str, object],
        _names: list[str],
    ) -> set[str]:
        nonlocal visual_calls
        idx = min(visual_calls, len(visual_by_call) - 1)
        visual_calls += 1
        return set(visual_by_call[idx])

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_found_word_regions_from_frame", visually_found)
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
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert ocr.region_id_calls == [
        # Scene detected from the word slot (no title OCR), then the help counter
        # on the same discovery tick.
        [
            "dreamscape_memory.1",
        ],
        [
            "dreamscape_memory.help.counter",
        ],
        # Scene locked after the first match; slot 1 found this tick → only the
        # help counter is read.
        [
            "dreamscape_memory.help.counter",
        ],
        # Wave turns active and the slot reopens → its word is read again.
        [
            "dreamscape_memory.1",
            "dreamscape_memory.help.counter",
        ],
    ]
    assert actions.taps == [(360, 512), (374, 384)]
    # Book is colour-confirmed (greyed on iteration 2) → clicked. The wave then
    # turns active and the slot reopens for Smoke, whose tap is dispatched but not
    # yet confirmed within the run, so only Book is recorded clicked.
    assert ctx.result["clicked"] == ["Book"]
    assert ctx.result["region_words"] == {"dreamscape_memory.1": "Smoke"}


@pytest.mark.asyncio
async def test_solve_loop_ocr_probes_closed_batch_when_visual_reopen_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the colour detector keeps reporting the solved pill as grey after the
    # next wave has loaded, pending_regions stays empty. The solver must still
    # probe the word buttons and reopen the batch when OCR sees a new word.
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {"dreamscape_memory.1": "Book"},
            {"dreamscape_memory.1": "Smoke"},
        ]
    )

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        lambda _image, _area_doc, _names: (
            {"dreamscape_memory.1"} if actions.taps else set()
        ),
    )
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
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert ocr.region_id_calls == [
        ["dreamscape_memory.1"],
        ["dreamscape_memory.help.counter"],
        ["dreamscape_memory.help.counter"],
        ["dreamscape_memory.1", "dreamscape_memory.help.counter"],
    ]
    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["clicked"] == ["Book"]
    assert ctx.result["region_words"] == {"dreamscape_memory.1": "Smoke"}
    assert any(
        event["kind"] == "batch_reset"
        and "OCR probe reopened" in event["message"]
        for event in ctx.result["events"]
    )


@pytest.mark.asyncio
async def test_solve_loop_does_not_reclick_found_slot_that_flickers_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both words are solved in one wave; afterwards the found slot 2 momentarily
    # mis-reads as active (detector flicker) while slot 1 stays struck. A found
    # slot never un-finds on its own inside a batch, so the solver must keep it
    # locked and not re-tap it — only a simultaneous flip of the WHOLE struck set
    # (the next word wave) may reopen the slots.
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {"dreamscape_memory.1": "Book", "dreamscape_memory.2": "Smoke"},
        ]
    )
    visual_by_call = [
        set(),  # iter1: nothing struck yet (before our clicks)
        {"dreamscape_memory.1", "dreamscape_memory.2"},  # iter2: both settle
        {"dreamscape_memory.1"},  # iter3: slot 2 flickers active, slot 1 struck
    ]
    visual_calls = 0

    def visually_found(
        _image: object,
        _area_doc: dict[str, object],
        _names: list[str],
    ) -> set[str]:
        nonlocal visual_calls
        idx = min(visual_calls, len(visual_by_call) - 1)
        visual_calls += 1
        return set(visual_by_call[idx])

    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_found_word_regions_from_frame", visually_found)
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
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # Each word clicked exactly once; the flicker on slot 2 produced no extra tap
    # and the slot stayed locked (still settled) rather than reopening.
    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["clicked"] == ["Book", "Smoke"]
    assert sorted(ctx.result["settled_regions"]) == [
        "dreamscape_memory.1",
        "dreamscape_memory.2",
    ]


@pytest.mark.asyncio
async def test_solve_loop_keeps_clicked_slot_locked_when_struck_ocr_turns_noisy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Background colour is the SOLE "found" signal now. When a clicked slot's OCR
    # turns into strike-through noise but the colour detector has not (yet)
    # confirmed it greyed-out, the slot stays locked: no re-tap, no helper, and it
    # is NOT marked settled. It simply waits for the colour detector.
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.1": "MeBookB",
                "dreamscape_memory.2": "aSmokej",
            },
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
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

    # Each word tapped once; the strike-through noise produced no extra tap and no
    # helper — the in-flight tap is locked, awaiting colour confirmation.
    assert actions.taps == [(360, 512), (374, 384)]
    # The colour detector (black fake frame) never confirmed → nothing settled and
    # nothing is recorded clicked. The taps simply stay in flight.
    assert ctx.result["settled_regions"] == []
    assert ctx.result["clicked_regions"] == []
    assert ctx.result["pending_click_regions"] == [
        "dreamscape_memory.1",
        "dreamscape_memory.2",
    ]
    assert ctx.result["click_retries"] == []
    assert ctx.result["unmapped"] == []
    assert ctx.result["helped"] == []


@pytest.mark.asyncio
async def test_solve_loop_defers_help_until_mapped_clicks_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
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
                "max_iterations": 1,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == [(360, 512)]
    assert ctx.result["unmapped"] == ["Smoke"]
    assert ctx.result["helped"] == []
    assert ctx.result["help_remaining"] == 2
    # Book's tap is dispatched (in flight, still ``determined``) — it is not
    # ``clicked`` until the colour confirms it. The helper is deferred this frame
    # because a mapped tap was just dispatched.
    assert ctx.result["slot_states"]["dreamscape_memory.1"]["status"] == "mapped"
    assert ctx.result["slot_states"]["dreamscape_memory.2"]["status"] == "unmapped"


@pytest.mark.asyncio
async def test_solve_loop_does_not_reclick_exhausted_key_before_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Book's tap lands and its pill greys → confirmed clicked on the next frame.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )
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
                "max_iterations": 4,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # Book is tapped once and colour-confirmed (so never re-tapped or re-clicked);
    # the helper then fires once for the unmapped Smoke.
    assert actions.taps == [(360, 512), (612, 1088)]
    assert ctx.result["skipped_clicked"] == []
    # Book confirmed cleanly → no exhausted/rejected taps.
    assert ctx.result["click_retry_exhausted"] == []


@pytest.mark.asyncio
async def test_solve_loop_taps_help_once_for_new_unmapped_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.2": "Smoke",
            },
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Book's tap lands and its pill greys → confirmed clicked on the next frame.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )
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
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == [(360, 512), (612, 1088)]
    assert actions.require_approval_values == [False, False]
    assert ctx.result["clicked_keys"] == ["book"]
    assert ctx.result["clicked_regions"] == ["dreamscape_memory.1"]
    # Book's tap was colour-confirmed → settled/clicked.
    assert ctx.result["settled_regions"] == ["dreamscape_memory.1"]
    assert ctx.result["pending_click_regions"] == []
    assert ctx.result["click_retries"] == []
    assert ctx.result["helped"] == ["Smoke"]
    assert ctx.result["helped_keys"] == ["smoke"]
    assert ctx.result["help_counter_reads"] == [2, 2]
    assert ctx.result["help_remaining"] == 1
    assert ctx.result["unmapped"] == ["Smoke"]
    assert ocr.region_id_calls == [
        # Scene detected from the word slots (no title OCR), then the help counter
        # on the same discovery tick.
        [
            "dreamscape_memory.1",
            "dreamscape_memory.2",
        ],
        [
            "dreamscape_memory.help.counter",
        ],
        # Book confirmed found on iteration 2 → its slot is skipped; only the
        # still-active Smoke and the helper counter are read.
        [
            "dreamscape_memory.2",
            "dreamscape_memory.help.counter",
        ],
    ]


@pytest.mark.asyncio
async def test_solve_loop_defers_help_for_single_read_unmapped_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A word seen on only one iteration (e.g. OCR of an animating slot) must not
    # spend a helper tap; the slot settles into a real, mappable word next frame.
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {"dreamscape_memory.1": "Smoke"},  # transient noise, vanishes next read
            {"dreamscape_memory.1": "Aurora"},  # settles into a mappable word
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(
        solve,
        "_select_scene",
        lambda _level_name, _fuzz_threshold: {
            "slug": "practice-level",
            "scene_rect": None,
            "points": [{"name": "Aurora", "xPct": 50.0, "yPct": 40.0}],
        },
    )

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # Only the real word (Aurora) is tapped; the helper button is never pressed.
    assert actions.taps == [(360, 512)]
    assert ctx.result["helped"] == []
    assert ctx.result["help_remaining"] == 2
    events = ctx.result["events"]
    assert any(e["kind"] == "helper_unconfirmed" for e in events)
    assert all(e["kind"] != "helper_click" for e in events)


@pytest.mark.asyncio
@pytest.mark.usefixtures("scene_db")
async def test_solve_loop_taps_help_highlight_without_saving_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    dreamscape_db.upsert_scene(
        "practice-level",
        title="Practice Level",
        source_image="ref.png",
        scene_rect=None,
        points=[{"n": 1, "name": "Book", "xPct": 50.0, "yPct": 40.0}],
        activate=True,
        season=0,
    )

    async def tap_help_target(
        actions_arg: object,
        instance_id: str,
        *,
        capture_delay_s: float,
        diff_gap_s: float,
        before_frame: object | None = None,
        word: str = "",
    ) -> object:
        assert capture_delay_s >= 0
        assert diff_gap_s >= 0
        assert before_frame is not None
        assert word
        point = solve.Point(180, 256)
        actions_arg.tap(instance_id, point, require_approval=False)
        return point

    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.2": "Smoke",
            },
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Book's tap lands and its pill greys → confirmed clicked on the next frame.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )
    monkeypatch.setattr(
        solve,
        "_select_scene",
        lambda _level_name, _fuzz_threshold: dreamscape_db.get_scene("practice-level"),
    )
    monkeypatch.setattr(solve, "_tap_help_highlight_target", tap_help_target)

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

    assert actions.taps == [(360, 512), (612, 1088), (180, 256)]
    assert actions.require_approval_values == [False, False, False]
    assert ctx.result["clicked_keys"] == ["book", "smoke"]
    assert ctx.result["help_target_taps"] == [{"word": "Smoke", "x": 180, "y": 256}]
    assert ctx.result["learned_help_points"] == []
    assert ctx.result["help_learn_errors"] == []
    scene = dreamscape_db.get_scene("practice-level")
    assert scene is not None
    assert scene["points"] == [{"n": 1, "name": "Book", "xPct": 50.0, "yPct": 40.0}]


@pytest.mark.asyncio
async def test_solve_loop_defers_help_for_low_confidence_unmapped_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stably-repeating but low-confidence read earns NO helper tap.

    Dialog bleed and frozen animation frames repeat identically at conf
    0.0-0.4; before the confidence floor they "confirmed" and burnt a helper
    ("В РАБ" → help). A genuine dark-chrome pill still gets help: the two-line
    retry's pass-agreement path raises its confidence to 0.8 first.
    """
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.3": "PocketWatch",
            },
            {
                "dreamscape_memory.3": "PocketWatch",
            }
        ],
        confidence_by_call=[
            {
                "dreamscape_memory.3": 0.0,
            },
            {
                "dreamscape_memory.3": 0.0,
            }
        ],
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
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
                "regions": [
                    "dreamscape_memory.1",
                    "dreamscape_memory.2",
                    "dreamscape_memory.3",
                ],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # Only the mapped Book tap; the conf-0.0 PocketWatch is surfaced as
    # unmapped but never confirms, so no helper tap is spent on it.
    assert actions.taps == [(360, 512)]
    assert ctx.result["unmapped"] == ["PocketWatch"]
    assert ctx.result["helped"] == []
    assert ctx.result["help_remaining"] == 2


@pytest.mark.asyncio
async def test_solve_loop_ignores_short_unmapped_ocr_garbage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.2": "Se",
            }
        ],
        confidence_by_call=[
            {
                "dreamscape_memory.2": 0.0,
            }
        ],
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
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
                "regions": ["dreamscape_memory.2"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 1,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == []
    assert ctx.result["unmapped"] == []
    assert ctx.result["helped"] == []
    assert ctx.result["help_remaining"] == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("scene_db")
async def test_solve_loop_taps_static_help_highlight_without_saving_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StaticHintActions(_FakeDreamscapeActions):
        def __init__(self) -> None:
            super().__init__()
            self.before = _paint_pill_band(np.zeros((1280, 720, 3), dtype=np.uint8))
            self.after = self.before.copy()
            cv2.circle(self.after, (325, 627), 44, (0, 180, 255), 10)
            cv2.circle(self.after, (325, 627), 58, (80, 120, 255), 6)
            self.help_requested = False

        def capture_screen_bgr_cached(
            self,
            _instance_id: str,
            *,
            max_age_ms: float,
        ) -> np.ndarray:
            assert max_age_ms >= 0
            return self.after if self.help_requested and max_age_ms == 0.0 else self.before

        def tap(
            self,
            _instance_id: str,
            point: object,
            *,
            require_approval: bool = True,
        ) -> bool:
            ok = super().tap(
                _instance_id,
                point,
                require_approval=require_approval,
            )
            if (point.x, point.y) == (612, 1088):
                self.help_requested = True
            return ok

    actions = _StaticHintActions()
    dreamscape_db.upsert_scene(
        "practice-level",
        title="Practice Level",
        source_image="ref.png",
        scene_rect=None,
        points=[{"n": 1, "name": "Book", "xPct": 50.0, "yPct": 40.0}],
        activate=True,
        season=0,
    )

    async def detect_terminal(_image, hint=None):
        return ""

    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[
            {
                "dreamscape_memory.1": "Book",
                "dreamscape_memory.2": "Smoke",
            },
            {
                "dreamscape_memory.2": "Smoke",
            },
        ]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(
        solve,
        "_select_scene",
        lambda _level_name, _fuzz_threshold: dreamscape_db.get_scene("practice-level"),
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
                "help_capture_delay": "0ms",
                "help_diff_gap": "0ms",
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps[:2] == [(360, 512), (612, 1088)]
    assert abs(actions.taps[2][0] - 325) <= 5
    assert abs(actions.taps[2][1] - 627) <= 5
    assert ctx.result["learned_help_points"] == []
    assert ctx.result["help_learn_errors"] == []
    scene = dreamscape_db.get_scene("practice-level")
    assert scene is not None
    assert [p["name"] for p in scene["points"]] == ["Book"]


@pytest.mark.asyncio
async def test_solve_loop_does_not_tap_help_when_counter_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr(help_counter="0")
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
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
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == [(360, 512)]
    assert ctx.result["helped"] == []
    assert ctx.result["help_counter_reads"] == [0, 0]
    assert ctx.result["help_remaining"] == 0
    assert ctx.result["unmapped"] == ["Smoke"]


@pytest.mark.asyncio
async def test_solve_loop_ignores_all_items_found_before_any_tap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    stop_reasons: list[str] = []

    async def detect_terminal(_image, hint=None):
        return solve._TERMINAL_ALL_FOUND

    def request_stop(reason: str) -> dict[str, object]:
        stop_reasons.append(reason)
        return {"requested": True, "mode": "embedded", "reason": reason}

    ocr = _FakeDreamscapeOcr()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(solve, "_request_local_bot_stop", request_stop)
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
                "max_iterations": 1,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["iterations"] == 1
    assert ctx.result["terminal_screen"] == ""
    assert ctx.result["status"] == "stopped"
    assert stop_reasons == []
    assert ctx.result["bot_stop"] == {}


@pytest.mark.asyncio
async def test_solve_loop_stops_on_all_items_found_after_tap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    detect_calls = 0
    stop_reasons: list[str] = []

    async def detect_terminal(_image, hint=None):
        nonlocal detect_calls
        detect_calls += 1
        return solve._TERMINAL_ALL_FOUND

    def request_stop(reason: str) -> dict[str, object]:
        stop_reasons.append(reason)
        return {"requested": True, "mode": "embedded", "reason": reason}

    ocr = _FakeDreamscapeOcr()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Both taps land and their pills grey → confirmed on iteration 2, clearing the
    # in-flight taps so the "all items found" terminal check can run.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(
            actions,
            {(360, 512): "dreamscape_memory.1", (374, 384): "dreamscape_memory.2"},
        ),
    )
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(solve, "_request_local_bot_stop", request_stop)
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
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert detect_calls == 1
    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["iterations"] == 2
    assert ctx.result["terminal_screen"] == solve._TERMINAL_ALL_FOUND
    assert ctx.result["status"] == "won"
    assert stop_reasons == [
        f"terminal screen detected: {solve._TERMINAL_ALL_FOUND}",
    ]
    assert ctx.result["bot_stop"] == {
        "requested": True,
        "mode": "embedded",
        "reason": f"terminal screen detected: {solve._TERMINAL_ALL_FOUND}",
    }


@pytest.mark.asyncio
async def test_solve_loop_requests_bot_stop_on_time_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """time_up after watched gameplay (words were read) ends the run as a loss."""
    actions = _FakeDreamscapeActions()
    stop_reasons: list[str] = []
    terminal_calls = {"n": 0}

    async def detect_terminal(_image, hint=None):
        # First tick: live round (no terminal). Later ticks: the timer ran out.
        terminal_calls["n"] += 1
        return "" if terminal_calls["n"] == 1 else solve._TERMINAL_TIME_UP

    def request_stop(reason: str) -> dict[str, object]:
        stop_reasons.append(reason)
        return {"requested": True, "mode": "embedded", "reason": reason}

    # One readable word on tick 1, nothing afterwards (pills gone with the round).
    ocr = _FakeDreamscapeOcr(
        word_values_by_call=[{"dreamscape_memory.1": "Book"}, {}, {}]
    )
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(solve, "_request_local_bot_stop", request_stop)
    # Confirm the tap via the colour detector so the slot settles — the
    # terminal check only runs once no tap is in flight.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )
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
                "max_iterations": 5,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert ctx.result["terminal_screen"] == solve._TERMINAL_TIME_UP
    assert ctx.result["status"] == "lost"
    assert stop_reasons == [
        f"terminal screen detected: {solve._TERMINAL_TIME_UP}",
    ]
    assert ctx.result["bot_stop"] == {
        "requested": True,
        "mode": "embedded",
        "reason": f"terminal screen detected: {solve._TERMINAL_TIME_UP}",
    }


@pytest.mark.asyncio
async def test_solve_loop_treats_start_screen_after_tap_as_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _FakeDreamscapeActions()
    detect_calls = 0
    stop_reasons: list[str] = []

    async def detect_terminal(_image, hint=None):
        nonlocal detect_calls
        detect_calls += 1
        return solve._START_SCREEN

    def request_stop(reason: str) -> dict[str, object]:
        stop_reasons.append(reason)
        return {"requested": True, "mode": "embedded", "reason": reason}

    ocr = _FakeDreamscapeOcr()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    # Both taps land and confirm on iteration 2, clearing the in-flight taps so
    # the terminal check can run.
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(
            actions,
            {(360, 512): "dreamscape_memory.1", (374, 384): "dreamscape_memory.2"},
        ),
    )
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(solve, "_request_local_bot_stop", request_stop)
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
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert detect_calls == 1
    assert actions.taps == [(360, 512), (374, 384)]
    assert ctx.result["terminal_screen"] == solve._START_SCREEN
    assert ctx.result["status"] == "won"
    assert stop_reasons == [
        f"terminal screen detected: {solve._START_SCREEN}",
    ]
    assert ctx.result["bot_stop"] == {
        "requested": True,
        "mode": "embedded",
        "reason": f"terminal screen detected: {solve._START_SCREEN}",
    }


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


# ── Pixel start gate (_round_started_pixels) ────────────────────────────────


def _gate_area_doc() -> dict:
    def region(name: str, x_pct: float, y_pct: float) -> dict:
        return {
            "name": name,
            "action": "text",
            "bbox": {
                "x": x_pct,
                "y": y_pct,
                "width": 25.0,
                "height": 3.0,
                "rotation": 0,
                "original_width": 720,
                "original_height": 1280,
            },
            "type": "string",
        }

    return {
        "screens": [
            {
                "id": 1,
                "regions": [
                    region("gate.1", 5.0, 88.0),
                    region("gate.2", 38.0, 88.0),
                    region("gate.3", 70.0, 88.0),
                ],
            }
        ]
    }


_GATE_REGIONS = ["gate.1", "gate.2", "gate.3"]


def _paint_slot(frame: np.ndarray, x_pct: float, y_pct: float) -> None:
    h, w = frame.shape[:2]
    x, y = int(x_pct / 100 * w), int(y_pct / 100 * h)
    frame[y : y + int(0.03 * h), x : x + int(0.25 * w)] = 255


def test_round_started_pixels_dark_shade_is_false() -> None:
    frame = np.full((1280, 720, 3), 40, dtype=np.uint8)
    assert solve._round_started_pixels(frame, _gate_area_doc(), _GATE_REGIONS) is False


def test_round_started_pixels_lit_slots_is_true() -> None:
    frame = np.full((1280, 720, 3), 40, dtype=np.uint8)
    _paint_slot(frame, 5.0, 88.0)
    _paint_slot(frame, 38.0, 88.0)
    assert solve._round_started_pixels(frame, _gate_area_doc(), _GATE_REGIONS) is True


def test_round_started_pixels_single_lit_slot_not_enough() -> None:
    # One bright slot can be a sparkle/animation artifact; the gate needs two.
    frame = np.full((1280, 720, 3), 40, dtype=np.uint8)
    _paint_slot(frame, 5.0, 88.0)
    assert solve._round_started_pixels(frame, _gate_area_doc(), _GATE_REGIONS) is False


def test_round_started_pixels_unresolvable_regions_is_none() -> None:
    frame = np.full((1280, 720, 3), 40, dtype=np.uint8)
    assert solve._round_started_pixels(frame, _gate_area_doc(), ["missing.1"]) is None
    assert solve._round_started_pixels(None, _gate_area_doc(), _GATE_REGIONS) is None


def test_round_started_pixels_live_multiplayer_reference_frame() -> None:
    image = cv2.imread(str(MODULE_DIR / "references" / "dreamscape_memory_.multiplayer.png"))
    assert image is not None
    area_doc = yaml.safe_load((MODULE_DIR / "area.yaml").read_text(encoding="utf-8"))
    regions = list(solve._DEFAULT_MULTIPLAYER_REGIONS)
    assert solve._round_started_pixels(image, area_doc, regions) is True
    # The same frame under the pre-round shade (uniformly darkened) reads dark.
    shaded = (image * 0.45).astype(np.uint8)
    assert solve._round_started_pixels(shaded, area_doc, regions) is False


def test_is_multiplayer_mode() -> None:
    assert solve._is_multiplayer_mode({"mode": "multiplayer"})
    assert solve._is_multiplayer_mode({"mode": " Co-Op "})
    assert not solve._is_multiplayer_mode({"mode": "solo"})
    assert not solve._is_multiplayer_mode({})


# --- localized item vocabulary -------------------------------------------
#
# Points are named in English; the RU client prints the same items in Russian.
# What matters is the wiring, not the wording: a Russian read must tap the exact
# pixel its English name taps, and must never steal a word a real item owns.


def _fake_aliases(monkeypatch, mapping: dict[str, list[str]]) -> None:
    monkeypatch.setattr(dreamscape_db, "item_aliases", lambda: mapping)


def test_localized_word_taps_the_canonical_point(monkeypatch) -> None:
    _fake_aliases(monkeypatch, {"rubber duck": ["резиновая уточка", "уточка"]})
    targets = solve._points_to_targets(
        [{"n": 1, "name": "Rubber duck", "xPct": 50, "yPct": 25}]
    )
    hits, misses = solve._resolve_taps(
        ["Резиновая уточка", "Уточка", "Rubber duck"], targets, 720, 1280
    )
    assert not misses
    assert {(p.x, p.y) for _word, p in hits} == {(360, 320)}


def test_localized_lookup_leaves_the_target_map_canonical(monkeypatch) -> None:
    """Aliases resolve at lookup time — they never enter the tap map itself."""
    _fake_aliases(monkeypatch, {"scarf": ["шарф"]})
    assert solve._points_to_targets(
        [{"n": 1, "name": "Scarf", "xPct": 40, "yPct": 60}]
    ) == {"scarf": (40.0, 60.0)}


def test_localized_alias_never_displaces_a_real_item(monkeypatch) -> None:
    """A scene that really contains "Шарф" keeps its own point for that word."""
    _fake_aliases(monkeypatch, {"cape": ["шарф"]})
    targets = solve._points_to_targets(
        [
            {"n": 1, "name": "Cape", "xPct": 10, "yPct": 10},
            {"n": 2, "name": "Шарф", "xPct": 80, "yPct": 80},
        ]
    )
    hits, _misses = solve._resolve_taps(["Шарф"], targets, 720, 1280)
    assert [(p.x, p.y) for _word, p in hits] == [(576, 1024)]


def test_garbled_localized_read_fuzzy_matches_within_its_own_alphabet(
    monkeypatch,
) -> None:
    """OCR noise on a Russian word recovers, and doesn't grab an English item."""
    _fake_aliases(monkeypatch, {"spider web": ["паутина"]})
    targets = solve._points_to_targets(
        [
            {"n": 1, "name": "Spider web", "xPct": 20, "yPct": 20},
            {"n": 2, "name": "Sword", "xPct": 90, "yPct": 90},
        ]
    )
    hits, misses = solve._resolve_taps(["Паутина"[:-1] + "а"], targets, 720, 1280)
    assert not misses
    assert [(p.x, p.y) for _word, p in hits] == [(144, 256)]


def test_unmapped_localized_word_is_reported_verbatim(monkeypatch) -> None:
    """A word with no wording yet stays a miss — the helper-learn flow gets it."""
    _fake_aliases(monkeypatch, {})
    targets = solve._points_to_targets([{"n": 1, "name": "Sword", "xPct": 90, "yPct": 90}])
    _hits, misses = solve._resolve_taps(["Табуретка"], targets, 720, 1280)
    assert misses == ["Табуретка"]


def test_shipped_item_lexicon_is_normalized() -> None:
    """The lexicon is keyed the way lookups are: normalized, no self-aliases."""
    aliases = dreamscape_db.item_aliases()
    assert aliases, "items_ru.yaml should ship a non-empty vocabulary"
    for name, localized in aliases.items():
        assert name == solve._normalize_word(name)
        assert localized, f"{name!r} maps to an empty alias list"
        for alias in localized:
            assert alias == solve._normalize_word(alias)
            assert alias != name


def test_scene_is_detected_from_localized_words_alone(monkeypatch) -> None:
    _fake_aliases(monkeypatch, {"corn": ["кукуруза"], "feather": ["перо"]})
    scenes = [
        {
            "slug": "garden",
            "season": 5,
            "names": dreamscape_db.aliased_names(["Corn", "Feather"]),
        },
        {"slug": "kitchen", "season": 5, "names": dreamscape_db.aliased_names(["Kettle"])},
    ]
    assert dreamscape_db.match_scene_by_words(["Кукуруза", "Перо"], scenes) == "garden"


# --- operator-picked scene (scene_source: active) -------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("scene_db")
async def test_solve_loop_scene_source_active_skips_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator picked the round: the active scene is locked from tick 1
    and word-based detection is never consulted."""
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Book", "xPct": 50.0, "yPct": 40.0}],
        activate=True,
    )
    actions = _FakeDreamscapeActions()
    ocr = _FakeDreamscapeOcr()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )

    def _boom(*_a: object, **_k: object) -> None:
        msg = "scene detection must not run under scene_source=active"
        raise AssertionError(msg)

    monkeypatch.setattr(solve, "_select_scene_ex", _boom)

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {
                "regions": ["dreamscape_memory.1"],
                "scene_source": "active",
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 2,
            }
            self.result: dict[str, object] = {}

        def fail(self, reason: str, **extra: object) -> None:
            self.result.update({"ok": False, "reason": reason, **extra})

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)
    assert ctx.result.get("scene") == "yard"
    assert actions.taps == [(360, 512)]


@pytest.mark.asyncio
async def test_solve_loop_scene_source_active_fails_without_active_scene(
    monkeypatch: pytest.MonkeyPatch, scene_db: object
) -> None:
    actions = _FakeDreamscapeActions()
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {"scene_source": "active", "ttl": "1s", "max_iterations": 1}
            self.result: dict[str, object] = {}

        def fail(self, reason: str, **extra: object) -> None:
            self.result.update({"ok": False, "reason": reason, **extra})

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)
    assert ctx.result == {
        "ok": False,
        "reason": "scene_source_active_without_active_scene",
    }


@pytest.mark.asyncio
async def test_solve_loop_ignores_stale_time_up_before_any_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A standalone/manual run can boot while the previous round's time-up is
    still on screen — it must be ignored until this run has read a word."""
    actions = _FakeDreamscapeActions()
    stop_reasons: list[str] = []

    async def detect_terminal(_image, hint=None):
        return solve._TERMINAL_TIME_UP

    ocr = _FakeDreamscapeOcr(word_values_by_call=[{}])
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(solve, "_detect_terminal_screen", detect_terminal)
    monkeypatch.setattr(
        solve,
        "_request_local_bot_stop",
        lambda reason: stop_reasons.append(reason),
    )
    monkeypatch.setattr(solve, "_select_scene", lambda _l, _f: None)

    class _Ctx:
        def __init__(self) -> None:
            self.redis_client = None
            self.player_id = ""
            self.instance_id = "bs1"
            self.args = {
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    # The stale screen never terminates the run; it ends on max_iterations.
    assert ctx.result["iterations"] == 3
    assert ctx.result["terminal_screen"] == ""
    assert stop_reasons == []


# --- helper-flow alias learning (scenes.db self-vocabulary) ----------------
#
# The in-game hint proves a (word -> position) pair; learn_point_alias persists
# it on the nearest scene point so the next round taps the word directly.


@pytest.mark.usefixtures("scene_db")
def test_learn_point_alias_snaps_to_the_nearest_point() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="",
        scene_rect={"left": 10.0, "top": 20.0, "width": 50.0, "height": 40.0},
        points=[
            {"n": 1, "name": "Clay Jug", "xPct": 50.0, "yPct": 50.0},
            {"n": 2, "name": "Saw", "xPct": 90.0, "yPct": 90.0},
        ],
        activate=True,
    )
    # Frame % for guide (52, 48): frame = rect + guide/100*size — within the
    # 3% snap radius of the point at (50, 50).
    res = dreamscape_db.learn_point_alias(
        "yard", word="Глиняный сосуд", frame_x_pct=10 + 0.52 * 50, frame_y_pct=20 + 0.48 * 40
    )
    assert res["learned"] is True
    assert res["point"] == "Clay Jug"
    scene = dreamscape_db.get_scene("yard")
    assert scene["points"][0]["aliases"] == ["Глиняный сосуд"]
    # The learned wording now taps the point (through the rect mapping).
    targets = solve._targets_for_scene(scene)
    assert targets["глиняный сосуд"] == targets["clay jug"]


@pytest.mark.usefixtures("scene_db")
def test_learn_point_alias_far_word_becomes_its_own_point() -> None:
    """The proven position is authoritative: far from every point, the word
    lands as a NEW point at exactly those coordinates — snapping to a
    merely-nearest point would tap the wrong pixel forever."""
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Saw", "xPct": 10.0, "yPct": 10.0}],
        activate=True,
    )
    far = dreamscape_db.learn_point_alias(
        "yard", word="Сосуд", frame_x_pct=90.0, frame_y_pct=90.0
    )
    assert far["learned"] is True
    assert far["new_point"] is True
    scene = dreamscape_db.get_scene("yard")
    assert scene["points"][-1] == {
        "n": 2, "name": "Сосуд", "xPct": 90.0, "yPct": 90.0, "learned": True,
    }
    targets = solve._targets_for_scene(scene)
    assert targets["сосуд"] == (90.0, 90.0)


@pytest.mark.usefixtures("scene_db")
def test_learn_point_alias_rejects_known_and_junk_words() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "Saw", "xPct": 10.0, "yPct": 10.0}],
        activate=True,
    )
    known = dreamscape_db.learn_point_alias(
        "yard", word="Saw", frame_x_pct=10.0, frame_y_pct=10.0
    )
    assert known == {"learned": False, "reason": "already_mapped"}
    junk = dreamscape_db.learn_point_alias(
        "yard", word="ыыыыыы", frame_x_pct=10.0, frame_y_pct=10.0
    )
    assert junk == {"learned": False, "reason": "implausible_word"}


@pytest.mark.usefixtures("scene_db")
def test_learned_alias_feeds_scene_detection() -> None:
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[
            {"n": 1, "name": "Saw", "xPct": 10.0, "yPct": 10.0},
            {"n": 2, "name": "Bell", "xPct": 30.0, "yPct": 30.0},
        ],
        activate=True,
    )
    dreamscape_db.learn_point_alias(
        "yard", word="Глиняный сосуд", frame_x_pct=11.0, frame_y_pct=11.0
    )
    index = dreamscape_db.scene_word_index()
    assert dreamscape_db.match_scene_by_words(
        ["Глиняный сосуд"], index["scenes"]
    ) == "yard"


@pytest.mark.usefixtures("scene_db")
def test_learn_point_alias_never_aliases_a_junk_point() -> None:
    """OCR debris in a scene ("a at") takes no vocabulary — the confirmed word
    becomes its own point instead of laundering the junk name."""
    dreamscape_db.upsert_scene(
        "yard", title="Yard", source_image="", scene_rect=None,
        points=[{"n": 1, "name": "a at", "xPct": 10.0, "yPct": 10.0}],
        activate=True,
    )
    res = dreamscape_db.learn_point_alias(
        "yard", word="Мешочек", frame_x_pct=10.0, frame_y_pct=10.0
    )
    assert res["learned"] is True
    assert res["new_point"] is True
    scene = dreamscape_db.get_scene("yard")
    assert scene["points"][0].get("aliases") is None
    assert scene["points"][-1]["name"] == "Мешочек"


# ── Pill template bank wiring ───────────────────────────────────────────────


def _paint_pill_word(frame: np.ndarray, text: str) -> np.ndarray:
    """White glyphs on the harness pill band so the bank has text to crop."""
    cv2.putText(
        frame, text, (5, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
    )
    return frame


@pytest.mark.asyncio
async def test_solve_loop_saves_pill_template_on_colour_confirm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A colour-confirmed tap persists the slot's ACTIVE rendering in the bank."""
    from games.wos.events.dreamscape_memory.solver.pill_bank import PillTemplateBank

    bank = PillTemplateBank(tmp_path / "bank")
    monkeypatch.setattr(solve, "_pill_bank", lambda: bank)
    actions = _FakeDreamscapeActions()
    _paint_pill_word(actions.frame, "BOOK")
    ocr = _FakeDreamscapeOcr(word_values_by_call=[{"dreamscape_memory.1": "Book"}])
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", _minimal_solver_area_doc)
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )
    monkeypatch.setattr(
        solve,
        "_select_scene",
        lambda _words, _fuzz: {
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
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                "max_iterations": 3,
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert ctx.result["clicked_keys"] == ["book"]
    assert ctx.result["pill_templates_saved"] == [{"key": "book", "word": "Book"}]
    # A fresh instance over the same directory sees the persisted template.
    assert PillTemplateBank(tmp_path / "bank").keys() == {"book"}


@pytest.mark.asyncio
async def test_solve_loop_pill_match_settles_slot_without_word_ocr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With a template banked, the slot is solved pre-OCR: no word-region reads."""
    from games.wos.events.dreamscape_memory.solver.pill_bank import PillTemplateBank

    actions = _FakeDreamscapeActions()
    _paint_pill_word(actions.frame, "BOOK")
    area_doc = _minimal_solver_area_doc()
    bank = PillTemplateBank(tmp_path / "bank")
    band = solve._slot_band_crop(actions.frame, area_doc, "dreamscape_memory.1")
    assert bank.store("book", "Book", band)
    monkeypatch.setattr(solve, "_pill_bank", lambda: bank)

    # No word values staged: any OCR read of a word slot would map nothing and
    # the tap below would never happen.
    ocr = _FakeDreamscapeOcr(word_values_by_call=[{}])
    monkeypatch.setattr(solve.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr(solve.dsl_runtime, "ocr_client", lambda: ocr)
    monkeypatch.setattr(solve, "_load_area", lambda: area_doc)
    monkeypatch.setattr(
        solve,
        "_found_word_regions_from_frame",
        _grey_when_point_tapped(actions, {(360, 512): "dreamscape_memory.1"}),
    )
    monkeypatch.setattr(
        dreamscape_db,
        "get_active_scene",
        lambda: {
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
                "regions": ["dreamscape_memory.1"],
                "ttl": "10s",
                "wait": "0ms",
                "tap_delay": "0ms",
                # Two ticks: match+tap, then colour confirm. A third tick would
                # start the settled-batch OCR probe, which reads word slots by
                # design (the clicked key is excluded from template matching).
                "max_iterations": 2,
                "scene_source": "active",
            }
            self.result: dict[str, object] = {}

    ctx = _Ctx()
    await solve._exec_dreamscape_memory_solve_loop(ctx)

    assert actions.taps == [(360, 512)]
    assert ctx.result["clicked_keys"] == ["book"]
    assert ctx.result["pill_matches"] == 1
    word_ids = {"dreamscape_memory.1", "dreamscape_memory.2", "dreamscape_memory.3"}
    assert all(
        rid not in word_ids for call in ocr.region_id_calls for rid in call
    ), ocr.region_id_calls
