"""kNN/digital — dataset layout, train, predict."""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from kNN.digital import (
    DigitClassifier,
    augment_glyph,
    build_training_matrices,
    estimate_digit_count,
    extract_labeled_glyphs,
    glyph_to_feature,
    model_path,
    parse_compact_number_text,
    parse_digit_count,
    recognize_compact_number_prediction,
    recognize_digits,
    recognize_digits_template,
    render_synthetic_digit,
    segment_digit_boxes,
)
from ocr.preprocess import resolve_preprocess


def test_glyph_to_feature_shape() -> None:
    g = render_synthetic_digit("3")
    feat = glyph_to_feature(g)
    assert feat.shape == (20 * 32,)
    assert feat.dtype == np.float32


def test_extract_labeled_glyphs_from_synthetic_strip() -> None:
    label = "765502864"
    cells = [render_synthetic_digit(ch, cell_h=28) for ch in label]
    strip = np.hstack(cells)
    glyphs = extract_labeled_glyphs(strip, label, x0=0)
    assert [ch for ch, _ in glyphs] == list(label)


def test_integer_type_resolves_to_knn_preprocess() -> None:
    assert resolve_preprocess(None, "integer") == "knn"


def test_parse_digit_count_auto_and_fixed() -> None:
    assert parse_digit_count(None) is None
    assert parse_digit_count("auto") is None
    assert parse_digit_count("9") == 9
    assert parse_digit_count(10) == 10


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("17492", 17_492),
        ("113,462", 113_462),
        ("41.8M", 41_800_000),
        ("31.1M", 31_100_000),
        ("1.2B", 1_200_000_000),
        ("95K", 95_000),
    ],
)
def test_parse_compact_number_text(text: str, expected: int) -> None:
    assert parse_compact_number_text(text) == expected


def test_estimate_digit_count_on_synthetic_strips() -> None:
    for label in ("12345678", "765502864", "1234567890"):
        cells = [render_synthetic_digit(ch, cell_h=28) for ch in label]
        strip = np.hstack(cells)
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip
        assert estimate_digit_count(gray, x0=0) == len(label)


def test_segment_auto_matches_narrow_runs_on_clean_strip() -> None:
    label = "765502864"
    cells = [render_synthetic_digit(ch, cell_h=28) for ch in label]
    strip = np.hstack(cells)
    gray = strip if strip.ndim == 2 else cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    boxes = segment_digit_boxes(gray, expected_count=None, x0=0)
    assert len(boxes) == len(label)


def test_train_predict_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    for digit in "0123456789":
        folder = root / digit
        folder.mkdir(parents=True)
        for i in range(5):
            g = render_synthetic_digit(digit, font_scale=0.5 + i * 0.03)
            seed = int(digit) * 17 + i
            for j, aug in enumerate(augment_glyph(g, seed=seed)):
                cv2.imwrite(str(folder / f"{i}_{j}.png"), aug)

    features, labels = build_training_matrices(root)
    clf = DigitClassifier.train_from_samples(features, labels, k=3)
    model_file = tmp_path / "model.yml"
    clf.save(model_file)
    loaded = DigitClassifier.load(model_file)

    query = render_synthetic_digit("7")
    pred = loaded.predict_glyphs([query])
    assert pred.text == "7"
    assert pred.confidence > 0.5


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fixture_name", "region_name", "expected", "digit_count"),
    [
        ("chief_profile_player_id_live.png", "player.id", "401227964", None),
        ("chief_profile_player_id_live.png", "player.state", "2558", 4),
        ("chief_profile_player_id_live_2.png", "player.id", "765502864", None),
        ("chief_profile_player_id_live_2.png", "player.state", "4353", 4),
        ("chief_profile_player_id_live_3.png", "player.id", "401227964", None),
        ("chief_profile_player_id_live_3.png", "player.state", "2558", 4),
    ],
)
def test_template_classifier_reads_live_digit_fixtures(
    fixture_name: str,
    region_name: str,
    expected: str,
    digit_count: int | None,
) -> None:
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc

    repo = Path(__file__).resolve().parents[3]
    area = load_area_doc(repo)
    image = cv2.imread(str(repo / "tests" / "fixtures" / fixture_name))
    assert image is not None
    pair = screen_region_by_name(area, region_name)
    assert pair is not None
    bbox = pair[1]["bbox"]
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    crop = image[py : py + ph, px : px + pw]

    text, conf = recognize_digits_template(crop, digit_count=digit_count, x0=0)
    assert text == expected
    assert conf >= 0.95


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fixture_name", "expected_text", "expected_value"),
    [
        ("chief_profile_player_id_live.png", "41.8M", "41800000"),
        ("chief_profile_player_id_live_2.png", "113,462", "113462"),
        ("chief_profile_player_id_live_3.png", "31.1M", "31100000"),
    ],
)
def test_compact_number_classifier_reads_live_power_fixtures(
    fixture_name: str,
    expected_text: str,
    expected_value: str,
) -> None:
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc

    repo = Path(__file__).resolve().parents[3]
    area = load_area_doc(repo)
    image = cv2.imread(str(repo / "tests" / "fixtures" / fixture_name))
    assert image is not None
    pair = screen_region_by_name(area, "player.power")
    assert pair is not None
    bbox = pair[1]["bbox"]
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    crop = image[py : py + ph, px : px + pw]

    pred = recognize_compact_number_prediction(crop)
    assert pred.text == expected_text
    assert pred.value_text == expected_value
    assert pred.confidence >= 0.95


@pytest.mark.integration
@pytest.mark.asyncio
async def test_production_model_reads_live_fixture() -> None:
    if not model_path().is_file():
        pytest.skip("kNN model not built — run scripts/train_knn_digital_model.py")

    from config.loader import load_settings, set_settings
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc
    from ocr.client import OcrClient

    set_settings(load_settings())
    repo = Path(__file__).resolve().parents[3]
    # Phase 3: root ``area.json`` is gone — every module ships its own
    # ``area.yaml`` and ``load_area_doc`` merges them. The ``player.id``
    # region lives under ``games/wos/core/chief_profile/area.yaml``.
    area = load_area_doc(repo)
    image = cv2.imread(str(repo / "tests/fixtures/chief_profile_player_id_live.png"))
    assert image is not None
    pair = screen_region_by_name(area, "player.id")
    assert pair is not None
    bbox = pair[1]["bbox"]
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    crop = image[py : py + ph, px : px + pw]

    text, conf = recognize_digits(crop, x0=0)
    assert text == "401227964"
    assert conf >= 0.9

    client = OcrClient(load_settings())
    from layout.types import Region

    result = await client.ocr_region(
        image,
        Region(px, py, pw, ph),
        preprocess="knn",
    )
    assert "401227964" in re.sub(r"\D+", "", result.text or "")
    assert result.confidence >= 0.9


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fixture_name", "region_name", "expected", "digit_count"),
    [
        ("chief_profile_player_id_live.png", "player.state", "2558", 4),
        ("chief_profile_player_id_live_2.png", "player.state", "4353", 4),
        ("chief_profile_player_id_live_3.png", "player.state", "2558", 4),
    ],
)
def test_production_model_reads_live_state_fixtures(
    fixture_name: str,
    region_name: str,
    expected: str,
    digit_count: int,
) -> None:
    if not model_path().is_file():
        pytest.skip("kNN model not built — run scripts/train_knn_digital_model.py")

    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc

    repo = Path(__file__).resolve().parents[3]
    area = load_area_doc(repo)
    image = cv2.imread(str(repo / "tests" / "fixtures" / fixture_name))
    assert image is not None
    pair = screen_region_by_name(area, region_name)
    assert pair is not None
    bbox = pair[1]["bbox"]
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    crop = image[py : py + ph, px : px + pw]

    text, conf = recognize_digits(crop, digit_count=digit_count, x0=0)
    assert text == expected
    assert conf >= 0.9
