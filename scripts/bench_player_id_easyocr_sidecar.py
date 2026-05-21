#!/usr/bin/env python3
"""EasyOCR-only sidecar for bench_player_id_ocr (Python 3.12 venv — torch has no py313 x86 wheels).

    uv venv .venv-easyocr-bench --python 3.12
    uv pip install --python .venv-easyocr-bench/bin/python easyocr opencv-python-headless "numpy<2"
    .venv-easyocr-bench/bin/python scripts/bench_player_id_easyocr_sidecar.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import easyocr

if TYPE_CHECKING:
    import numpy as np

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "chief_profile_player_id_live.png"
AREA_JSON = REPO / "area.json"
EXPECTED = "401227964"


def _digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _bbox_crop(image: np.ndarray, bbox: dict) -> np.ndarray:
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    return image[py : py + ph, px : px + pw].copy()


def _clahe_otsu_x3(crop: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.resize(binary, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_LINEAR)


def main() -> int:
    area = json.loads(AREA_JSON.read_text())
    bbox = next(
        r["bbox"]
        for s in area["screens"]
        if s.get("screen_id") == "chief_profile"
        for r in s["regions"]
        if r.get("name") == "player.id"
    )
    image = cv2.imread(str(FIXTURE))
    if image is None:
        print("failed to load fixture", file=sys.stderr)
        return 1
    crop = _bbox_crop(image, bbox)
    print(f"easyocr sidecar | crop {crop.shape[1]}x{crop.shape[0]}")

    t0 = time.perf_counter()
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    print(f"reader load: {1000 * (time.perf_counter() - t0):.0f} ms")

    variants = [
        ("raw", crop),
        ("binary_x3", cv2.cvtColor(_clahe_otsu_x3(crop), cv2.COLOR_GRAY2BGR)),
    ]
    for name, work in variants:
        rgb = cv2.cvtColor(work, cv2.COLOR_BGR2RGB)
        t1 = time.perf_counter()
        raw = reader.readtext(rgb, detail=1, paragraph=False)
        ms = 1000 * (time.perf_counter() - t1)
        texts, confs = [], []
        for _bb, txt, conf in raw:
            t = str(txt).strip()
            if t:
                texts.append(t)
                confs.append(float(conf))
        text = "".join(texts)
        conf = sum(confs) / len(confs) if confs else 0.0
        digits = _digits(text)
        ok = digits == EXPECTED and conf >= 0.9
        print(
            f"  {name:<12} conf={conf:.3f} ms={ms:.1f} ok={'yes' if ok else 'no'} "
            f"text={text!r} digits={digits!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
