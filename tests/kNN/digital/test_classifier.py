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
    parse_digit_count,
    recognize_digits,
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
@pytest.mark.asyncio
async def test_production_model_reads_live_fixture() -> None:
    if not model_path().is_file():
        pytest.skip("kNN model not built — run scripts/train_knn_digital_model.py")

    import json

    from config.loader import load_settings, set_settings
    from layout.area_lookup import screen_region_by_name
    from ocr.client import OcrClient

    set_settings(load_settings())
    repo = Path(__file__).resolve().parents[3]
    area = json.loads((repo / "area.json").read_text())
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
