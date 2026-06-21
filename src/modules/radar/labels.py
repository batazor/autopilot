"""Building-name registry from a city scan.

On pan, the city overlays each building's NAME on a dark translucent plate
(white text). The radar captures the frame right after the swipe, so those
plates land in the saved frames. This module reads a completed run, detects the
plates, OCRs the names, places each into canvas coordinates via the per-frame
canvas positions the stitcher wrote to ``map_meta.json``, and de-dups the same
building seen across overlapping frames into one entry.

The output (``buildings.json``) is what a navigator routes over: ``name`` +
``canvas_px``. Image quality is irrelevant here — only the text and position
matter — so this works on the same frames regardless of how the map looks.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch_georef import MAP_META_NAME

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

BUILDINGS_NAME = "buildings.json"

# A label pixel is near-white on EVERY channel. Snow is blue-grey (min channel
# ~200), comfortably under this; plate text is ~250 (mirrors label_guard).
_WHITE_MIN = 232
# Plate text geometry after merging characters into a line (crop-space px).
_MIN_W, _MIN_H, _MAX_H, _MIN_AR = 22, 8, 44, 1.5
# OCR words below this mean-confidence are dropped as plate noise / cut text.
_MIN_CONF = 55.0
# Same NAME seen this close (canvas px) is one building — generous, because a
# plate floats above its building and stitch error shifts it frame to frame.
_DEDUP_PX = 200.0
# Two readings THIS close are the same physical plate regardless of text — it is
# OCR character noise (Iron/lron/tron Mine), not two buildings sharing a spot.
_SAME_SPOT_PX = 50.0
# Drop sub-this-length fragments ("ral", "Exp") left after merging.
_MIN_NAME_LEN = 4


@dataclass
class Building:
    name: str
    canvas_px: tuple[float, float]
    confidence: float
    sightings: int = 1
    _names: list[tuple[str, float]] = field(default_factory=list)
    _sum: tuple[float, float] = (0.0, 0.0)
    _wsum: float = 0.0


def _norm(name: str) -> str:
    """Lowercase letters+spaces only (OCR punctuation/digits → separators)."""
    cleaned = "".join(c if c.isalpha() else " " for c in name).lower()
    return " ".join(cleaned.split())


def _clean(name: str) -> str:
    """Tidy an OCR reading for display: punctuation → space, drop a leading
    digit token (icon-number bleed). ``4 Lancer Camp`` → ``Lancer Camp``,
    ``Beast'Cage`` → ``Beast Cage``, ``Lighthouse,`` → ``Lighthouse``."""
    toks = "".join(c if (c.isalnum() or c.isspace()) else " " for c in name).split()
    while toks and toks[0].isdigit():
        toks.pop(0)
    return " ".join(toks)


def _compatible(a: str, b: str) -> bool:
    """True when two normalized names are the same building, tolerating cut text
    (one a prefix/substring of the other — ``infantry cam`` ↔ ``infantry camp``)."""
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _tesseract_cmd() -> str:
    try:
        from config.loader import load_settings

        cmd = str(getattr(load_settings().ocr, "tesseract_cmd", "") or "").strip()
    except Exception:
        cmd = ""
    return cmd or "tesseract"


def _ocr_line(crop: np.ndarray, tess: str) -> tuple[str, float]:
    """OCR a single text line → (text, mean word confidence 0–100)."""
    if crop is None or crop.size == 0:
        return "", 0.0
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return "", 0.0
    try:
        out = subprocess.run(
            [tess, "stdin", "stdout", "-l", "eng", "--psm", "7", "tsv"],
            input=buf.tobytes(),
            capture_output=True,
            check=False,
            timeout=15,
        ).stdout.decode("utf-8", "ignore")
    except (OSError, subprocess.SubprocessError):
        return "", 0.0
    words, confs = [], []
    for row in csv.DictReader(io.StringIO(out), delimiter="\t"):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1.0
        if text and conf >= 0:
            words.append(text)
            confs.append(conf)
    if not words:
        return "", 0.0
    return " ".join(words), float(np.mean(confs))


def _looks_like_name(text: str) -> bool:
    letters = sum(c.isalpha() for c in text)
    return len(text) >= 3 and letters >= 3 and letters >= 0.6 * len(text.replace(" ", ""))


def detect_labels(frame: np.ndarray, crop: dict, tess: str) -> list[dict]:
    """Find building-name plates in one frame → ``[{name, confidence, frame_px}]``.

    ``frame_px`` is the label centre in FULL-frame pixels (crop offset folded
    in) so the caller can place it on the canvas directly.
    """
    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    roi = frame[y : y + h, x : x + w]
    b, g, r = cv2.split(roi)
    white = ((b > _WHITE_MIN) & (g > _WHITE_MIN) & (r > _WHITE_MIN)).astype(np.uint8) * 255
    # Merge characters of one name into a single horizontal blob.
    joined = cv2.morphologyEx(
        white, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5))
    )
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(joined, 8)
    out: list[dict] = []
    for i in range(1, count):
        bx, by, bw, bh, area = stats[i]
        if bw < _MIN_W or not (_MIN_H <= bh <= _MAX_H) or bw / bh < _MIN_AR or area < 60:
            continue
        pad = 4
        sub = roi[max(by - pad, 0) : by + bh + pad, max(bx - pad, 0) : bx + bw + pad]
        sub = cv2.resize(sub, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        text, conf = _ocr_line(sub, tess)
        if conf < _MIN_CONF or not _looks_like_name(text):
            continue
        out.append(
            {
                "name": text,
                "confidence": round(conf, 1),
                "frame_px": [int(x + bx + bw / 2), int(y + by + bh / 2)],
            }
        )
    return out


def _dedup(detections: Iterable[dict]) -> list[Building]:
    """Merge detections of the same building seen across overlapping frames.

    Two detections merge when their names are compatible (same building, cut
    text tolerated) AND they sit within ``_DEDUP_PX`` on the canvas. Position is
    the confidence-weighted mean of all sightings; the canonical name is the
    longest reading (most complete), tie-broken by confidence.
    """
    buildings: list[Building] = []
    for det in sorted(detections, key=lambda d: -d["confidence"]):
        cx, cy = det["canvas_px"]
        norm = _norm(det["name"])
        hit = next(
            (
                bld
                for bld in buildings
                if (
                    (d2 := (bld.canvas_px[0] - cx) ** 2 + (bld.canvas_px[1] - cy) ** 2)
                    <= _SAME_SPOT_PX**2
                )
                or (
                    d2 <= _DEDUP_PX**2
                    and any(_compatible(norm, _norm(n)) for n, _ in bld._names)
                )
            ),
            None,
        )
        if hit is None:
            hit = Building(name=det["name"], canvas_px=(cx, cy), confidence=det["confidence"])
            buildings.append(hit)
        else:
            hit.sightings += 1
        hit._names.append((det["name"], det["confidence"]))
        wx, wy = hit._sum
        conf = max(det["confidence"], 1.0)
        hit._sum = (wx + cx * conf, wy + cy * conf)
        hit._wsum += conf
        hit.canvas_px = (hit._sum[0] / hit._wsum, hit._sum[1] / hit._wsum)
        # Canonical reading: WoS names are Title Case ("Iron Mine"), so prefer
        # proper capitalisation (beats the misread "lron Mine"), then a real
        # multi-word name, then length, then confidence.
        best = max(hit._names, key=lambda nc: _name_score(*nc))
        hit.name, hit.confidence = best[0].strip(), best[1]
    return buildings


def _name_score(name: str, conf: float) -> tuple:
    toks = name.split()
    proper = bool(toks) and all(t[:1].isupper() for t in toks)
    return (proper, len(toks) > 1, len(name.strip()), conf)


def build_registry(run_dir: Path, crop: dict | None = None) -> dict:
    """Extract the building registry for a completed run.

    Reads ``map_meta.json`` (per-frame canvas positions) + the frame PNGs, OCRs
    the name plates, places each building in canvas px, de-dups, and writes
    ``buildings.json``. ``crop`` defaults to the run manifest's config crop.
    """
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / MAP_META_NAME).read_text(encoding="utf-8"))
    frame_pos = meta.get("frames") or {}
    if not frame_pos:
        msg = f"{run_dir/MAP_META_NAME} has no per-frame canvas positions — re-stitch the run"
        raise ValueError(msg)
    if crop is None:
        manifest = json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        crop = (manifest.get("config") or {}).get("crop")
    if not crop:
        msg = "no crop available (pass crop= or ensure the manifest carries config.crop)"
        raise ValueError(msg)

    tess = _tesseract_cmd()
    if shutil.which(tess) is None and not Path(tess).exists():
        msg = f"tesseract not found: {tess!r}"
        raise RuntimeError(msg)

    detections: list[dict] = []
    for key, fp in frame_pos.items():
        path = run_dir / f"frame_{key}.png"
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        ox, oy = fp["canvas_px"]
        for det in detect_labels(frame, crop, tess):
            fx, fy = det["frame_px"]
            detections.append(
                {
                    "name": det["name"],
                    "confidence": det["confidence"],
                    "canvas_px": [round(ox + fx, 1), round(oy + fy, 1)],
                }
            )

    buildings = _dedup(detections)
    registry = _registry_dict(buildings)
    (run_dir / BUILDINGS_NAME).write_text(json.dumps(registry, indent=2), encoding="utf-8")
    logger.info("radar: building registry — %d buildings → %s", len(buildings), BUILDINGS_NAME)
    return registry


def _registry_dict(buildings: list[Building]) -> dict:
    rows = [
        {
            "name": _clean(b.name),
            "canvas_px": [round(b.canvas_px[0], 1), round(b.canvas_px[1], 1)],
            "confidence": b.confidence,
            "sightings": b.sightings,
        }
        for b in sorted(buildings, key=lambda b: (b.canvas_px[1], b.canvas_px[0]))
        if len(_clean(b.name)) >= _MIN_NAME_LEN
    ]
    return {"count": len(rows), "buildings": rows}


def _unique_positions(items: Iterable[tuple[str, tuple[float, float]]]) -> dict:
    """Map name→position for names that appear EXACTLY once (safe anchors)."""
    seen: dict[str, tuple[float, float]] = {}
    dupes: set[str] = set()
    for name, pos in items:
        if name in seen:
            dupes.add(name)
        else:
            seen[name] = pos
    return {n: p for n, p in seen.items() if n not in dupes}


def _align_offset(master: list[Building], new: list[dict]) -> tuple[float, float] | None:
    """Translation that brings ``new`` into the master frame, from buildings
    that are uniquely named in BOTH (so the match is unambiguous). The median
    delta is robust to a stray mismatch. ``None`` when they share no anchor."""
    mu = _unique_positions((_norm(b.name), b.canvas_px) for b in master)
    nu = _unique_positions((_norm(b["name"]), tuple(b["canvas_px"])) for b in new)
    deltas = [(mu[n][0] - nu[n][0], mu[n][1] - nu[n][1]) for n in mu if n in nu]
    if not deltas:
        return None
    xs = sorted(d[0] for d in deltas)
    ys = sorted(d[1] for d in deltas)
    return (xs[len(xs) // 2], ys[len(ys) // 2])


def _canvas_offset(base: np.ndarray, new: np.ndarray) -> tuple[float, float] | None:
    """ORB-match ``new`` onto ``base``; return the shift mapping a ``new`` pixel
    to its ``base`` pixel (``base = new + shift``), or None without enough overlap.
    Robust to OCR noise — it aligns IMAGERY, not building names."""
    orb = cv2.ORB_create(4000)
    ka, da = orb.detectAndCompute(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY), None)
    kb, db = orb.detectAndCompute(cv2.cvtColor(new, cv2.COLOR_BGR2GRAY), None)
    if da is None or db is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(db, da)
    good = [m for m in matches if m.distance < 48]
    if len(good) < 12:
        return None
    pb = np.float32([kb[m.queryIdx].pt for m in good])
    pa = np.float32([ka[m.trainIdx].pt for m in good])
    _m, inl = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC, ransacReprojThreshold=3)
    if inl is None or int(inl.sum()) < 10:
        return None
    inl = inl.ravel().astype(bool)
    shift = np.median(pa[inl] - pb[inl], axis=0)
    return (float(shift[0]), float(shift[1]))


def merge_runs_by_image(run_dirs: list[Path]) -> dict:
    """Fuse scans into one registry by aligning their stitched CANVASES with ORB
    (not building names — those are OCR-noisy). Each run needs ``map_full.png`` +
    ``buildings.json``. A run is placed if its canvas visually overlaps any
    already-placed run; isolated runs (no shared imagery) are skipped."""
    runs = []
    for d in run_dirs:
        d = Path(d)
        img = cv2.imread(str(d / "map_full.png"))
        bj = d / BUILDINGS_NAME
        if img is None or not bj.is_file():
            continue
        runs.append((d.name, img, json.loads(bj.read_text(encoding="utf-8")).get("buildings") or []))
    if not runs:
        return _registry_dict([])
    runs.sort(key=lambda r: -len(r[2]))  # most buildings first = base frame
    placed: list[tuple[np.ndarray, tuple[float, float]]] = [(runs[0][1], (0.0, 0.0))]
    dets = [{"name": b["name"], "confidence": b.get("confidence", 90.0),
             "canvas_px": list(b["canvas_px"])} for b in runs[0][2]]
    for name, img, blds in runs[1:]:
        off = None
        for pimg, poff in placed:
            sh = _canvas_offset(pimg, img)
            if sh is not None:
                off = (sh[0] + poff[0], sh[1] + poff[1])
                break
        if off is None:
            logger.warning("merge-by-image: %s overlaps no placed scan — skipped", name)
            continue
        dets.extend(
            {
                "name": b["name"],
                "confidence": b.get("confidence", 90.0),
                "canvas_px": [b["canvas_px"][0] + off[0], b["canvas_px"][1] + off[1]],
            }
            for b in blds
        )
        placed.append((img, off))
    return _registry_dict(_dedup(dets))


def merge_registries(registries: list[dict]) -> dict:
    """Fuse per-scan registries into one, aligning each to the growing master by
    the buildings they share (overlapping scans). Scans that share no uniquely
    named building with the master are skipped (can't be placed)."""
    # Largest registry first: it has the most anchors, so it becomes the base
    # frame and a tiny/garbage scan can never seed the master (then drop every
    # real one for "no shared anchor").
    ordered = sorted(registries, key=lambda r: -len(r.get("buildings") or []))
    master: list[Building] = []
    for i, reg in enumerate(ordered):
        new = reg.get("buildings") or []
        if not new:
            continue
        off = (0.0, 0.0) if not master else _align_offset(master, new)
        if off is None:
            logger.warning("merge: registry %d shares no anchor with the master — skipped", i)
            continue
        dets = [
            {
                "name": b["name"],
                "confidence": b.get("confidence", 90.0),
                "canvas_px": [b["canvas_px"][0] + off[0], b["canvas_px"][1] + off[1]],
            }
            for b in new
        ]
        carry = [
            {"name": b.name, "confidence": b.confidence, "canvas_px": list(b.canvas_px)}
            for b in master
        ]
        master = _dedup(carry + dets)
    return _registry_dict(master)
