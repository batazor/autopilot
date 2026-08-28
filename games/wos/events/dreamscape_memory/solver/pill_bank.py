"""Self-assembling template bank for Dreamscape word pills.

A word pill's rendering is pixel-deterministic at the fixed 720x1280
resolution: the same word draws the same glyphs at the same offsets every
round, in every slot. Tesseract on live frames regularly shreds that text
(«ша» for «Шарики»), while template matching on the *rendered* pill is
essentially exact — measured over 86 labeled slot crops from real frames,
same-word pairs score ≥0.999 TM_CCOEFF_NORMED with ≥0.997 text-mask IoU while
the closest different-word pair («мяч» vs «меч») stays at 0.914 / 0.807.

The bank collects templates on its own: whenever the solve loop's tap on a
word is colour-confirmed (the pill greys inside the learn window), the tight
text crop of that pill — taken from the lossless frame the word was read on —
is persisted under the word's canonical key. On later rounds the slot is
matched against the templates of the keys still expected in the scene BEFORE
any OCR; a confident match settles the slot's word without Tesseract.

Storage is a flat directory (PNG per template + ``index.json``) so both the
worker and the standalone runner (no Redis) share one global bank: the pill
for a word looks identical in every scene.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, NamedTuple

from .constants import (
    _PILL_BG_ACTIVE_BGR,
    _PILL_BG_MAX_REF_DIST,
    _PILL_BG_STRUCK_BGR,
    _PILL_TMPL_IOU_THR,
    _PILL_TMPL_MARGIN_PX,
    _PILL_TMPL_MAX_VARIANTS,
    _PILL_TMPL_MIN_TEXT_PX,
    _PILL_TMPL_SCORE_THR,
    _PILL_TMPL_TEXT_THR,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9а-яё]+")


class PillMatch(NamedTuple):
    key: str
    word: str
    score: float
    iou: float


class PillTemplate(NamedTuple):
    key: str
    word: str
    file: str
    gray: Any  # np.ndarray, HxW uint8
    mask: Any  # np.ndarray, HxW bool


def _slug(key: str) -> str:
    return _SLUG_RE.sub("-", key.strip().casefold()).strip("-") or "word"


def _pill_state(crop: Any) -> str:
    """``active`` / ``struck`` / ``none`` — nearest-centroid on the pill fill.

    Same classification the solve loop uses for the colour-confirm detector
    (median over the brighter half of the inner band); duplicated here so the
    bank stays importable without ``exec.py``.
    """
    if crop is None or not hasattr(crop, "shape") or len(crop.shape) != 3:
        return "none"
    import cv2
    import numpy as np

    height, width = int(crop.shape[0]), int(crop.shape[1])
    if width < 20 or height < 8:
        return "none"
    x1, x2 = int(round(width * 0.08)), int(round(width * 0.92))
    y1, y2 = int(round(height * 0.18)), int(round(height * 0.82))
    inner = crop[y1:y2, x1:x2]
    if inner.size == 0:
        return "none"
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    mask = gray > gray.mean()
    if int(mask.sum()) < 20:
        return "none"
    bg = np.array([float(np.median(inner[..., ch][mask])) for ch in range(3)])
    dist_active = float(np.linalg.norm(bg - np.array(_PILL_BG_ACTIVE_BGR)))
    dist_struck = float(np.linalg.norm(bg - np.array(_PILL_BG_STRUCK_BGR)))
    if min(dist_active, dist_struck) > _PILL_BG_MAX_REF_DIST:
        return "none"
    return "active" if dist_active < dist_struck else "struck"


def _text_tight_crop(crop_bgr: Any) -> tuple[Any, Any] | None:
    """``(gray, mask)`` tight crop of the pill's white text, or ``None``.

    The white glyphs are the only near-white pixels inside the slot band (the
    pale-lavender fill sits well below the threshold), so a plain binary
    threshold plus bounding box isolates exactly the rendered word — the part
    that differs between pills — and drops the fill/border that made
    whole-band matching confuse «мяч» with «меч» at 0.965.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, _PILL_TMPL_TEXT_THR, 255, cv2.THRESH_BINARY)
    ys, xs = np.nonzero(binary)
    if len(xs) < _PILL_TMPL_MIN_TEXT_PX:
        return None
    x0 = max(0, int(xs.min()) - _PILL_TMPL_MARGIN_PX)
    y0 = max(0, int(ys.min()) - _PILL_TMPL_MARGIN_PX)
    x1 = min(gray.shape[1], int(xs.max()) + 1 + _PILL_TMPL_MARGIN_PX)
    y1 = min(gray.shape[0], int(ys.max()) + 1 + _PILL_TMPL_MARGIN_PX)
    return gray[y0:y1, x0:x1], binary[y0:y1, x0:x1] > 0


def _match_one(template: PillTemplate, search_gray: Any) -> tuple[float, float] | None:
    """``(score, iou)`` of the template's best placement in the search band."""
    import cv2
    import numpy as np

    tg = template.gray
    if tg.shape[0] > search_gray.shape[0] or tg.shape[1] > search_gray.shape[1]:
        return None
    res = cv2.matchTemplate(search_gray, tg, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    x, y = loc
    patch = search_gray[y : y + tg.shape[0], x : x + tg.shape[1]]
    patch_mask = patch > _PILL_TMPL_TEXT_THR
    union = int(np.count_nonzero(np.logical_or(template.mask, patch_mask)))
    if union == 0:
        return None
    inter = int(np.count_nonzero(np.logical_and(template.mask, patch_mask)))
    return float(score), inter / union


class PillTemplateBank:
    """Global word→pill-template store under one directory.

    Layout: ``<root>/index.json`` + ``<root>/<slug>__<n>.png`` (grayscale
    tight text crops). The bank is append-only in normal operation and safe to
    delete wholesale — it re-assembles itself from confirmed taps.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._index_path = self._root / "index.json"
        self._index_mtime: float | None = None
        self._templates: dict[str, list[PillTemplate]] = {}

    # ── loading ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            mtime = self._index_path.stat().st_mtime
        except OSError:
            self._templates = {}
            self._index_mtime = None
            return
        if mtime == self._index_mtime:
            return
        import cv2

        templates: dict[str, list[PillTemplate]] = {}
        try:
            entries = json.loads(self._index_path.read_text(encoding="utf-8")).get(
                "templates", []
            )
        except (OSError, ValueError):
            logger.warning("pill_bank: unreadable index %s", self._index_path, exc_info=True)
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            file = str(entry.get("file") or "")
            if not key or not file:
                continue
            gray = cv2.imread(str(self._root / file), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                logger.warning("pill_bank: missing template image %s", file)
                continue
            templates.setdefault(key, []).append(
                PillTemplate(
                    key=key,
                    word=str(entry.get("word") or key),
                    file=file,
                    gray=gray,
                    mask=gray > _PILL_TMPL_TEXT_THR,
                )
            )
        self._templates = templates
        self._index_mtime = mtime

    def keys(self) -> set[str]:
        self._load()
        return set(self._templates)

    # ── matching ─────────────────────────────────────────────────────────

    def match(self, crop_bgr: Any, keys: set[str]) -> PillMatch | None:
        """Best confident template match of an ACTIVE pill among ``keys``.

        Only an active (not struck, not covered) pill band is matched: the
        templates hold the active rendering, and a slot that is not showing a
        pill must fall through to the OCR path untouched. Thresholds carry a
        wide margin over the closest measured impostor pair — see the module
        docstring — so a hit is authoritative and OCR can be skipped.
        """
        self._load()
        if not self._templates or not keys:
            return None
        if _pill_state(crop_bgr) != "active":
            return None
        import cv2

        search_gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        best: PillMatch | None = None
        for key in keys:
            for template in self._templates.get(key, ()):
                scored = _match_one(template, search_gray)
                if scored is None:
                    continue
                score, iou = scored
                if score < _PILL_TMPL_SCORE_THR or iou < _PILL_TMPL_IOU_THR:
                    continue
                if best is None or (score, iou) > (best.score, best.iou):
                    if best is not None and best.key != key:
                        logger.warning(
                            "pill_bank: two keys passed the match gate (%r, %r) — "
                            "taking the higher score",
                            best.key,
                            key,
                        )
                    best = PillMatch(key=key, word=template.word, score=score, iou=iou)
        return best

    # ── storing ──────────────────────────────────────────────────────────

    def store(self, key: str, word: str, crop_bgr: Any, *, source: str = "") -> bool:
        """Persist the pill crop as a template for ``key`` (idempotent).

        Returns ``True`` when a new template was written. Refuses crops that
        are not an active pill, crops with no extractable text, duplicates an
        existing template already matches, and keys at the variant cap.
        """
        key = str(key or "").strip()
        if not key:
            return False
        if _pill_state(crop_bgr) != "active":
            return False
        tight = _text_tight_crop(crop_bgr)
        if tight is None:
            return False
        gray, mask = tight
        self._load()
        existing = self._templates.get(key, [])
        import cv2

        search_gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        for template in existing:
            scored = _match_one(template, search_gray)
            if (
                scored is not None
                and scored[0] >= _PILL_TMPL_SCORE_THR
                and scored[1] >= _PILL_TMPL_IOU_THR
            ):
                return False  # this rendering is already covered
        if len(existing) >= _PILL_TMPL_MAX_VARIANTS:
            logger.warning(
                "pill_bank: key %r at variant cap (%d) with an unmatched rendering — "
                "not storing",
                key,
                _PILL_TMPL_MAX_VARIANTS,
            )
            return False

        self._root.mkdir(parents=True, exist_ok=True)
        slug = _slug(key)
        n = 0
        while (self._root / f"{slug}__{n}.png").exists():
            n += 1
        file = f"{slug}__{n}.png"
        if not cv2.imwrite(str(self._root / file), gray):
            logger.warning("pill_bank: imwrite failed for %s", file)
            return False

        entries = []
        with contextlib.suppress(OSError, ValueError):
            entries = json.loads(self._index_path.read_text(encoding="utf-8")).get(
                "templates", []
            )
        entries.append(
            {
                "key": key,
                "word": str(word or key),
                "file": file,
                "w": int(gray.shape[1]),
                "h": int(gray.shape[0]),
                "source": source,
                "created_at": time.time(),
            }
        )
        payload = json.dumps({"version": 1, "templates": entries}, ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(dir=self._root, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            Path(tmp).replace(self._index_path)
        except OSError:
            logger.warning("pill_bank: index write failed", exc_info=True)
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
            return False
        self._templates.setdefault(key, []).append(
            PillTemplate(key=key, word=str(word or key), file=file, gray=gray, mask=mask)
        )
        try:
            self._index_mtime = self._index_path.stat().st_mtime
        except OSError:
            self._index_mtime = None
        logger.info("pill_bank: stored template %s for key=%r word=%r", file, key, word)
        return True
