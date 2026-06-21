#!/usr/bin/env python3
"""Update Season 3 Dreamscape scene point coordinates from numbered guide PNGs.

The Season 3 reference images draw item numbers as white digits on black rounded
blocks. The older import path used full-image sparse Tesseract OCR, which misses
many of these labels on busy art. This script first detects the black/white label
regions, OCRs only those crops, then updates existing DB points by ``n`` while
leaving any undetected points untouched.

Usage:

    uv run python games/wos/events/dreamscape_memory/tools/update_s3_marker_positions.py --dry-run
    uv run python games/wos/events/dreamscape_memory/tools/update_s3_marker_positions.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np

from config import dreamscape_db
from config.loader import load_settings
from config.paths import repo_root

_MODULE_REL = "games/wos/events/dreamscape_memory"
_MAPS_DIR = repo_root() / _MODULE_REL / "references" / "maps"
_DEFAULT_DEBUG_DIR = repo_root() / ".tmp_ocr" / "dreamscape_memory_digits" / "s3_update_debug"


@dataclass(frozen=True)
class Candidate:
    value: int
    x: float
    y: float
    box: tuple[int, int, int, int]
    source: str
    priority: int
    area: int
    attempts: tuple[tuple[str, str, int], ...] = ()

    @property
    def x_pct(self) -> float:
        msg = "x_pct requires image width; compute at call site"
        raise AttributeError(msg)


def _tess_text(
    work: np.ndarray,
    *,
    psm: int,
    tesseract_cmd: str,
    lang: str,
    tessdata_dir: str,
) -> str:
    ok, buf = cv2.imencode(".png", work)
    if not ok or buf is None:
        return ""
    cmd = [
        tesseract_cmd,
        "stdin",
        "stdout",
        "-l",
        lang,
        "--oem",
        "1",
        "--psm",
        str(psm),
        "-c",
        "tessedit_char_whitelist=0123456789",
    ]
    if tessdata_dir:
        cmd.extend(["--tessdata-dir", tessdata_dir])
    proc = subprocess.run(
        cmd,
        input=buf.tobytes(),
        capture_output=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return re.sub(r"\D+", "", proc.stdout.decode("utf-8", errors="replace"))


def _normalize_marker_text(
    text: str,
    *,
    valid_numbers: set[int],
    expected_len: int | None,
) -> int | None:
    if not text:
        return None
    # S3 labels are at most two digits. Three digits are usually a false leading
    # edge from a rounded block (e.g. 130 -> 30); four+ digits are background OCR.
    if len(text) > 3:
        return None
    if (
        expected_len
        and len(text) == expected_len
        and text.isdigit()
        and (expected_len == 1 or not text.startswith("0"))
    ):
        value = int(text)
        return value if value in valid_numbers else None

    lengths: list[int] = []
    if expected_len:
        lengths.append(expected_len)
    lengths.extend([2, 1])
    seen: set[int] = set()
    for length in lengths:
        if length in seen or len(text) < length:
            continue
        seen.add(length)
        pieces = [text[-length:], text[:length]]
        if len(text) > length:
            pieces.extend(text[i : i + length] for i in range(1, len(text) - length + 1))
        for piece in pieces:
            if not piece.isdigit() or (length > 1 and piece.startswith("0")):
                continue
            value = int(piece)
            if value in valid_numbers:
                return value

    if not expected_len and text.isdigit():
        value = int(text)
        return value if value in valid_numbers else None
    return None


def _ocr_box(
    image: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    valid_numbers: set[int],
    expected_len: int | None,
    tesseract_cmd: str,
    lang: str,
    tessdata_dir: str,
) -> tuple[int | None, tuple[tuple[str, str, int], ...]]:
    img_h, img_w = image.shape[:2]
    x, y, w, h = map(int, box)
    x = max(0, x)
    y = max(0, y)
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    if x2 <= x or y2 <= y:
        return None, ()

    crop = image[y:y2, x:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants = [
        ("raw", crop),
        ("otsu", cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)),
        ("otsu_inv", cv2.cvtColor(cv2.bitwise_not(binary), cv2.COLOR_GRAY2BGR)),
    ]

    attempts: list[tuple[str, str, int]] = []
    for name, base in variants:
        work = cv2.resize(base, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
        work = cv2.copyMakeBorder(
            work, 35, 35, 35, 35, cv2.BORDER_CONSTANT, value=(255, 255, 255)
        )
        for psm in (8, 13, 7, 10):
            text = _tess_text(
                work,
                psm=psm,
                tesseract_cmd=tesseract_cmd,
                lang=lang,
                tessdata_dir=tessdata_dir,
            )
            if not text:
                continue
            attempts.append((text, name, psm))
            value = _normalize_marker_text(
                text, valid_numbers=valid_numbers, expected_len=expected_len
            )
            if value is not None:
                return value, tuple(attempts)

    for text, _name, _psm in attempts:
        value = _normalize_marker_text(
            text, valid_numbers=valid_numbers, expected_len=expected_len
        )
        if value is not None:
            return value, tuple(attempts)
    return None, tuple(attempts)


def _group_digit_components(
    components: list[tuple[int, int, int, int]],
) -> list[list[tuple[int, int, int, int]]]:
    parent = list(range(len(components)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def vertical_overlap(
        a: tuple[int, int, int, int], b: tuple[int, int, int, int]
    ) -> float:
        _ax, ay, _aw, ah = a
        _bx, by, _bw, bh = b
        return max(0, min(ay + ah, by + bh) - max(ay, by)) / float(min(ah, bh))

    def horizontal_gap(
        a: tuple[int, int, int, int], b: tuple[int, int, int, int]
    ) -> int:
        ax, _ay, aw, _ah = a
        bx, _by, bw, _bh = b
        return bx - (ax + aw) if ax <= bx else ax - (bx + bw)

    for i, a in enumerate(components):
        for j in range(i + 1, len(components)):
            b = components[j]
            max_gap = max(24, int(0.55 * max(a[3], b[3])))
            if vertical_overlap(a, b) >= 0.43 and 0 <= horizontal_gap(a, b) <= max_gap:
                union(i, j)

    groups: dict[int, list[tuple[int, int, int, int]]] = {}
    for i, comp in enumerate(components):
        groups.setdefault(find(i), []).append(comp)
    return list(groups.values())


def _white_digit_candidates(
    image: np.ndarray,
    *,
    valid_numbers: set[int],
    mode: str,
    tesseract_cmd: str,
    lang: str,
    tessdata_dir: str,
) -> list[Candidate]:
    img_h, img_w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    if mode == "white_strict":
        gray_thr, sat_thr, dark_thr, min_dark, priority = 165, 85, 45, 0.45, 0
    else:
        gray_thr, sat_thr, dark_thr, min_dark, priority = 150, 180, 70, 0.35, 1

    mask = ((gray > gray_thr) & (hsv[:, :, 1] < sat_thr)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8), iterations=1
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    components: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        fill = area / float(w * h) if w * h else 0.0
        if not (8 <= w <= 70 and 18 <= h <= 95 and fill > 0.10):
            continue
        pad = 14
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(img_w, x + w + pad)
        y2 = min(img_h, y + h + pad)
        if float((gray[y1:y2, x1:x2] < dark_thr).mean()) < min_dark:
            continue
        components.append((x, y, w, h))

    out: list[Candidate] = []
    for parts in _group_digit_components(components):
        x1 = min(x for x, _y, _w, _h in parts)
        y1 = min(y for _x, y, _w, _h in parts)
        x2 = max(x + w for x, _y, w, _h in parts)
        y2 = max(y + h for _x, y, _w, h in parts)
        expected_len = 1 if len(parts) == 1 else 2
        px = 20 if x2 - x1 < 45 else 16
        py = 16
        box = (x1 - px, y1 - py, (x2 - x1) + 2 * px, (y2 - y1) + 2 * py)
        value, attempts = _ocr_box(
            image,
            box,
            valid_numbers=valid_numbers,
            expected_len=expected_len,
            tesseract_cmd=tesseract_cmd,
            lang=lang,
            tessdata_dir=tessdata_dir,
        )
        if value is None:
            continue
        out.append(
            Candidate(
                value=value,
                x=(x1 + x2) / 2.0,
                y=(y1 + y2) / 2.0,
                box=box,
                source=mode,
                priority=priority,
                area=(x2 - x1) * (y2 - y1),
                attempts=attempts[:4],
            )
        )
    return out


def _dark_block_candidates(
    image: np.ndarray,
    *,
    valid_numbers: set[int],
    tesseract_cmd: str,
    lang: str,
    tessdata_dir: str,
) -> list[Candidate]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    out: list[Candidate] = []
    for threshold in (45, 55, 65):
        mask = (gray < threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
            iterations=1,
        )
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            fill = area / float(w * h) if w * h else 0.0
            if not (25 <= w <= 150 and 22 <= h <= 110 and fill > 0.35):
                continue
            roi = gray[y : y + h, x : x + w]
            if float((roi > 145).mean()) < 0.03:
                continue
            if any(
                abs(x - cand.box[0]) < 5
                and abs(y - cand.box[1]) < 5
                and abs(w - cand.box[2]) < 8
                and abs(h - cand.box[3]) < 8
                for cand in out
            ):
                continue
            value, attempts = _ocr_box(
                image,
                (x, y, w, h),
                valid_numbers=valid_numbers,
                expected_len=None,
                tesseract_cmd=tesseract_cmd,
                lang=lang,
                tessdata_dir=tessdata_dir,
            )
            if value is None:
                continue
            glyph = (roi > 145).astype(np.uint8) * 255
            glyph_contours, _ = cv2.findContours(
                glyph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            xs: list[int] = []
            ys: list[int] = []
            for gc in glyph_contours:
                gx, gy, gw, gh = cv2.boundingRect(gc)
                if gw >= 3 and gh >= 8:
                    xs.extend([x + gx, x + gx + gw])
                    ys.extend([y + gy, y + gy + gh])
            cx = (min(xs) + max(xs)) / 2.0 if xs else x + w / 2.0
            cy = (min(ys) + max(ys)) / 2.0 if ys else y + h / 2.0
            out.append(
                Candidate(
                    value=value,
                    x=cx,
                    y=cy,
                    box=(x, y, w, h),
                    source="dark",
                    priority=2,
                    area=w * h,
                    attempts=attempts[:4],
                )
            )
    return out


def detect_markers(image: np.ndarray, valid_numbers: set[int]) -> tuple[dict[int, Candidate], list[int]]:
    settings = load_settings()
    candidates: list[Candidate] = []
    candidates.extend(
        _white_digit_candidates(
            image,
            valid_numbers=valid_numbers,
            mode="white_strict",
            tesseract_cmd=settings.ocr.tesseract_cmd,
            lang=settings.ocr.lang,
            tessdata_dir=settings.ocr.tessdata_dir,
        )
    )
    candidates.extend(
        _white_digit_candidates(
            image,
            valid_numbers=valid_numbers,
            mode="white_broad",
            tesseract_cmd=settings.ocr.tesseract_cmd,
            lang=settings.ocr.lang,
            tessdata_dir=settings.ocr.tessdata_dir,
        )
    )
    candidates.extend(
        _dark_block_candidates(
            image,
            valid_numbers=valid_numbers,
            tesseract_cmd=settings.ocr.tesseract_cmd,
            lang=settings.ocr.lang,
            tessdata_dir=settings.ocr.tessdata_dir,
        )
    )

    candidates.sort(key=lambda c: (c.value, c.priority, c.area))
    selected: dict[int, Candidate] = {}
    far_dups: list[int] = []
    for cand in candidates:
        if cand.value not in valid_numbers:
            continue
        if cand.value not in selected:
            selected[cand.value] = cand
            continue
        prev = selected[cand.value]
        dist = ((prev.x - cand.x) ** 2 + (prev.y - cand.y) ** 2) ** 0.5
        if dist > 40:
            far_dups.append(cand.value)
    return selected, sorted(set(far_dups))


def _draw_debug(image: np.ndarray, selected: dict[int, Candidate], path: Path) -> None:
    img_h, img_w = image.shape[:2]
    vis = image.copy()
    for number, cand in selected.items():
        x, y, w, h = cand.box
        color = (0, 255, 0) if cand.source != "dark" else (0, 210, 255)
        cv2.rectangle(
            vis,
            (max(0, x), max(0, y)),
            (min(img_w - 1, x + w), min(img_h - 1, y + h)),
            color,
            2,
        )
        label = str(number)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
        ly = max(0, y - th - 8)
        lx = max(0, x)
        cv2.rectangle(vis, (lx, ly), (lx + tw + 8, ly + th + base + 8), (0, 0, 0), -1)
        cv2.putText(
            vis,
            label,
            (lx + 4, ly + th + 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            color,
            2,
            cv2.LINE_AA,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), vis)


def _backup_db(debug_dir: Path) -> Path:
    backup_dir = debug_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"scenes.db.before_s3_marker_update.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(dreamscape_db.db_path(), backup)
    return backup


def _checkpoint_db() -> None:
    conn = sqlite3.connect(str(dreamscape_db.db_path()))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _scene_paths(slugs: list[str] | None) -> list[Path]:
    paths = sorted(_MAPS_DIR.glob("*-s3/*-s3.png"))
    if slugs:
        wanted = {s.strip() for s in slugs if s.strip()}
        paths = [p for p in paths if p.parent.name in wanted]
    return paths


def run(*, write: bool, debug_dir: Path, slugs: list[str] | None) -> dict[str, Any]:
    backup = str(_backup_db(debug_dir)) if write else ""
    rows: list[dict[str, Any]] = []
    for image_path in _scene_paths(slugs):
        slug = image_path.parent.name
        scene = dreamscape_db.get_scene(slug)
        if scene is None:
            print(f"{slug:16s} skipped: no DB scene")
            continue
        points = list(scene["points"])
        valid_numbers = {int(p.get("n", 0)) for p in points if int(p.get("n", 0)) > 0}
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"{slug:16s} skipped: unreadable image")
            continue
        img_h, img_w = image.shape[:2]
        selected, far_dups = detect_markers(image, valid_numbers)
        missing = sorted(valid_numbers - set(selected))

        if write:
            for point in points:
                n = int(point.get("n", 0))
                cand = selected.get(n)
                if cand is None:
                    continue
                point["xPct"] = round(cand.x / img_w * 100.0, 2)
                point["yPct"] = round(cand.y / img_h * 100.0, 2)
            dreamscape_db.upsert_scene(
                slug,
                title=str(scene.get("title") or slug),
                source_image=str(scene.get("source_image") or image_path),
                scene_rect=scene.get("scene_rect"),
                points=points,
                activate=bool(scene.get("active")),
                archived=bool(scene.get("archived")),
                season=int(scene.get("season") or 3),
                images=scene.get("images") or None,
            )

        debug_image = debug_dir / f"{slug}.png"
        _draw_debug(image, selected, debug_image)
        sources = {
            source: sum(1 for cand in selected.values() if cand.source == source)
            for source in ("white_strict", "white_broad", "dark")
        }
        row = {
            "slug": slug,
            "point_count": len(points),
            "detected": len(selected),
            "missing": missing,
            "far_dups": far_dups,
            "sources": sources,
            "debug_image": str(debug_image),
            "markers": {
                str(n): {
                    "xPct": round(cand.x / img_w * 100.0, 2),
                    "yPct": round(cand.y / img_h * 100.0, 2),
                    "source": cand.source,
                    "box": list(cand.box),
                    "attempts": [list(a) for a in cand.attempts],
                }
                for n, cand in sorted(selected.items())
            },
        }
        rows.append(row)
        verb = "updated" if write else "detected"
        print(
            f"{slug:16s} {verb}={len(selected):2d}/{len(points):2d} "
            f"missing={len(missing):2d} sources={sources} debug={debug_image}"
        )
        if missing:
            print(f"  kept old coords for: {missing}" if write else f"  missing: {missing}")

    if write:
        _checkpoint_db()
    summary = {
        "mode": "write" if write else "dry-run",
        "backup": backup,
        "updated_at": time.time(),
        "maps": rows,
    }
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary={debug_dir / 'summary.json'}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="update scenes.db")
    mode.add_argument("--dry-run", action="store_true", help="detect only; do not update DB")
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=_DEFAULT_DEBUG_DIR,
        help="directory for debug PNGs and summary.json",
    )
    parser.add_argument(
        "--slug",
        action="append",
        default=None,
        help="scene slug to process; may be passed multiple times",
    )
    args = parser.parse_args()
    run(write=bool(args.write), debug_dir=args.debug_dir, slugs=args.slug)


if __name__ == "__main__":
    main()
