#!/usr/bin/env python3
"""Benchmark Tesseract vs EasyOCR on ``player.id`` (chief_profile live fixture).

Usage (EasyOCR pulls torch on first install):

    uv run --with easyocr python scripts/bench_player_id_ocr.py

Optional:

    uv run --with easyocr python scripts/bench_player_id_ocr.py \\
        --fixture tests/fixtures/chief_profile_player_id_live.png
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from typing import TYPE_CHECKING  # noqa: E402

from config.loader import load_settings, set_settings  # noqa: E402
from kNN.digital.paths import model_path as knn_model_path  # noqa: E402
from layout.area_lookup import screen_region_by_name  # noqa: E402
from layout.types import Region  # noqa: E402
from ocr.client import OcrClient  # noqa: E402
from ocr.preprocess import binary_tile_for_ocr, enhance_for_ocr  # noqa: E402

if TYPE_CHECKING:
    import numpy as np

DEFAULT_FIXTURE = REPO / "tests" / "fixtures" / "chief_profile_player_id_live.png"
AREA_JSON = REPO / "area.json"
EXPECTED_DIGITS = "401227964"
AREA_THRESHOLD = 0.9


@dataclass(frozen=True)
class BenchRow:
    backend: str
    preprocess: str
    text: str
    digits: str
    confidence: float
    ms: float
    passes_threshold: bool


def _region_px_from_area(
    image: np.ndarray, area_doc: dict, region_name: str = "player.id"
) -> Region:
    h, w = int(image.shape[0]), int(image.shape[1])
    pair = screen_region_by_name(area_doc, region_name)
    if pair is None:
        msg = f"region {region_name!r} not found in area.json"
        raise ValueError(msg)
    bbox = pair[1]["bbox"]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    return Region(px, py, pw, ph)


def _crop_bgr(image: np.ndarray, region: Region) -> np.ndarray:
    hi, wi = int(image.shape[0]), int(image.shape[1])
    x1 = max(0, min(int(region.x), wi))
    y1 = max(0, min(int(region.y), hi))
    x2 = max(x1, min(int(region.x + region.w), wi))
    y2 = max(y1, min(int(region.y + region.h), hi))
    return image[y1:y2, x1:x2].copy()


def _digits(text: str) -> str:
    return re.sub(r"\D+", "", text or "")


async def _bench_tesseract(
    image: np.ndarray,
    region: Region,
    *,
    client: OcrClient,
    preprocess: str | None,
    label: str,
) -> BenchRow:
    t0 = time.perf_counter()
    result = await client.ocr_region(image, region, preprocess=preprocess)
    ms = 1000.0 * (time.perf_counter() - t0)
    digits = _digits(result.text)
    conf = float(result.confidence)
    return BenchRow(
        backend="tesseract",
        preprocess=label,
        text=(result.text or "").strip(),
        digits=digits,
        confidence=conf,
        ms=ms,
        passes_threshold=conf >= AREA_THRESHOLD and digits == EXPECTED_DIGITS,
    )


def _easyocr_read(crop_bgr: np.ndarray, reader: object) -> tuple[str, float]:
    """Return combined text and mean box confidence from EasyOCR."""
    # EasyOCR expects RGB
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    t0 = time.perf_counter()
    raw = reader.readtext(rgb, detail=1, paragraph=False)  # type: ignore[attr-defined]
    _ = 1000.0 * (time.perf_counter() - t0)
    if not raw:
        return "", 0.0
    texts: list[str] = []
    confs: list[float] = []
    for _bbox, txt, conf in raw:
        t = str(txt or "").strip()
        if not t:
            continue
        texts.append(t)
        confs.append(float(conf))
    combined = "".join(texts).strip()
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return combined, mean_conf


def _bench_easyocr(
    crop_bgr: np.ndarray,
    reader: object,
    *,
    preprocess: str,
    work: np.ndarray,
) -> BenchRow:
    t0 = time.perf_counter()
    text, conf = _easyocr_read(work, reader)
    ms = 1000.0 * (time.perf_counter() - t0)
    digits = _digits(text)
    return BenchRow(
        backend="easyocr",
        preprocess=preprocess,
        text=text,
        digits=digits,
        confidence=conf,
        ms=ms,
        passes_threshold=conf >= AREA_THRESHOLD and digits == EXPECTED_DIGITS,
    )


def _print_table(rows: list[BenchRow]) -> None:
    print(f"\nExpected digits: {EXPECTED_DIGITS}  |  area.json threshold: {AREA_THRESHOLD}")
    print(f"{'backend':<10} {'preprocess':<14} {'conf':>7} {'ms':>8} {'ok':>4}  text")
    print("-" * 72)
    for r in rows:
        ok = "yes" if r.passes_threshold else "no"
        print(
            f"{r.backend:<10} {r.preprocess:<14} {r.confidence:7.3f} {r.ms:8.1f} {ok:>4}  "
            f"{r.text!r}  (digits={r.digits!r})"
        )
    best = max(rows, key=lambda r: (r.passes_threshold, r.confidence, -r.ms))
    print(f"\nBest (pass first, then conf, then speed): {best.backend} / {best.preprocess}")


async def _async_main(fixture: Path) -> int:
    if not fixture.is_file():
        print(f"fixture missing: {fixture}", file=sys.stderr)
        return 1
    if not AREA_JSON.is_file():
        print(f"area.json missing: {AREA_JSON}", file=sys.stderr)
        return 1

    set_settings(load_settings())
    area_doc = json.loads(AREA_JSON.read_text(encoding="utf-8"))
    image = cv2.imread(str(fixture))
    if image is None:
        print(f"failed to read image: {fixture}", file=sys.stderr)
        return 1

    region = _region_px_from_area(image, area_doc)
    crop = _crop_bgr(image, region)
    print(f"fixture: {fixture.relative_to(REPO)}")
    print(f"crop: {crop.shape[1]}x{crop.shape[0]} px  region=({region.x},{region.y},{region.w}x{region.h})")

    client = OcrClient(load_settings())
    rows: list[BenchRow] = []

    for label, pre in [
        ("raw", None),
        ("fast_line", "fast_line"),
        ("enhance", "enhance"),
        ("digits", "digits"),
    ]:
        rows.append(
            await _bench_tesseract(image, region, client=client, preprocess=pre, label=label)
        )

    if knn_model_path().is_file():
        from kNN.digital import get_classifier

        t0 = time.perf_counter()
        pred = get_classifier().predict_strip(crop, digit_count=9, x0=0)
        ms = 1000.0 * (time.perf_counter() - t0)
        rows.append(
            BenchRow(
                backend="knn",
                preprocess="cv2.ml",
                text=pred.text,
                digits=re.sub(r"\D+", "", pred.text),
                confidence=pred.confidence,
                ms=ms,
                passes_threshold=pred.confidence >= AREA_THRESHOLD
                and _digits(pred.text) == EXPECTED_DIGITS,
            )
        )

    sidecar_venv = REPO / ".venv-easyocr-bench" / "bin" / "python"
    sidecar_script = REPO / "scripts" / "bench_player_id_easyocr_sidecar.py"
    if sidecar_venv.is_file() and sidecar_script.is_file():
        import subprocess

        print("\n--- EasyOCR (sidecar venv py3.12; torch has no cp313 x86 wheels) ---")
        proc = await asyncio.to_thread(
            subprocess.run,
            [str(sidecar_venv), str(sidecar_script)],
            cwd=str(REPO),
            check=False,
            text=True,
        )
        if proc.returncode != 0:
            print(f"EasyOCR sidecar exited {proc.returncode}", file=sys.stderr)
        _print_table(rows)
        return 0

    try:
        import easyocr
    except ImportError:
        print(
            "\nEasyOCR: not in project venv (Python 3.13 + torch wheels). "
            "For EasyOCR rows run once:\n"
            "  uv venv .venv-easyocr-bench --python 3.12\n"
            "  uv pip install --python .venv-easyocr-bench/bin/python "
            'easyocr opencv-python-headless "numpy<2"\n'
            "  uv run python scripts/bench_player_id_ocr.py"
        )
        _print_table(rows)
        return 0

    print("\nLoading EasyOCR reader (first run may download models)…")
    t_load = time.perf_counter()
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    load_ms = 1000.0 * (time.perf_counter() - t_load)
    print(f"EasyOCR reader ready in {load_ms:.0f} ms")

    rows.append(_bench_easyocr(crop, reader, preprocess="raw", work=crop))
    enh_bgr = cv2.cvtColor(enhance_for_ocr(crop), cv2.COLOR_GRAY2BGR)
    rows.append(_bench_easyocr(crop, reader, preprocess="enhance_bgr", work=enh_bgr))
    bin_bgr = cv2.cvtColor(binary_tile_for_ocr(crop), cv2.COLOR_GRAY2BGR)
    rows.append(_bench_easyocr(crop, reader, preprocess="binary_x3", work=bin_bgr))

    # warmup excluded from row ms; second read for steady-state latency
    _ = _easyocr_read(crop, reader)
    rows.append(_bench_easyocr(crop, reader, preprocess="raw_2nd", work=crop))

    _print_table(rows)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Full-screen PNG (default: live chief_profile fixture)",
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(args.fixture.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
