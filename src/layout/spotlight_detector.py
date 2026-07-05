"""Generic tutorial-spotlight detector — template-free, locale-agnostic.

The game's first-run tutorial darkens most of the screen and leaves a bright
"cutout" highlighted around the UI element it wants tapped (often with a
pulsing glow and/or a pointing hand), plus a translucent instruction caption
banner. This is the *topological inverse* of the blurred-scrim modal that
:mod:`popup.mask` localizes: there a sharp card sits on a smooth scrim; here a
bright cutout sits on a uniformly **darkened** scrim. We reuse the same CV
machinery (HSV V-channel masks + ``connectedComponentsWithStats`` + ring
sampling) used across ``layout/green_button_detector`` and ``popup/mask``.

Two orthogonal signals, both validated on a live ``Белая мгла`` frame
(720×1280, bs4):

* **Spatial** (single frame, the analyzer gate): a uniformly dim scrim
  (``scrim_frac`` of the frame below ``v_scrim``) enclosing one compact bright
  cutout with a **sharp rim** (inside-V minus surrounding-ring-V ≥ ``rim_step``).
  A wide+short bright blob low on the screen is classified as the caption
  banner (a confidence booster) and excluded from tap candidates. On the
  reference frame: ``scrim_frac≈0.40``, cutout rim step ``≈133`` at centroid
  ``(360, 1156)``, caption banner ``664×119`` at ``y≈965``.

* **Temporal** (two frames, the exec tap-target picker): during a tutorial the
  whole screen is frozen except the pulsing highlight, so a frame diff
  concentrates on the tap target. On the reference frames the diff centroid was
  ``(365, 1149)`` — i.e. exactly the compact cutout, not the static caption.

Every kernel is width-derived so the detector survives non-720×1280 frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2  # type: ignore[import-untyped]
import numpy as np


@dataclass(frozen=True, slots=True)
class SpotlightConfig:
    """Tunable thresholds. A dataclass (not module constants) so the overlay
    rule and the exec handler can override per deployment without monkeypatching.
    """

    # Scrim: a pixel is "dimmed" when its HSV Value is at or below this.
    v_scrim: int = 80
    # Cutout: a pixel is "lit" when its HSV Value is at or above this.
    v_bright: int = 160
    # A lit component qualifies as a cutout only within this area band
    # (fraction of the whole frame) — rejects single pixels and full panels.
    min_cutout_frac: float = 0.002
    max_cutout_frac: float = 0.30
    # Sharp rim: lit-inside mean V minus surrounding-ring mean V must clear this.
    rim_step_min: float = 60.0
    # Ring sampled outside the cutout bbox, as a fraction of frame width.
    ring_pad_frac: float = 0.05
    # Hard gate: at least this fraction of the frame must be dimmed, else it is
    # an ordinary screen with a bright element (not a spotlight overlay).
    scrim_floor: float = 0.18
    # scrim_frac is mapped 0→1 across this band for the confidence blend.
    scrim_lo: float = 0.18
    scrim_hi: float = 0.55
    # rim_step is mapped 0→1 across [0, rim_full] for the confidence blend.
    rim_full: float = 120.0
    # Reject when there are more than this many comparable lit blobs (that is a
    # normal bright UI / night map of markers, not one spotlight).
    max_bright_blobs: int = 6
    # Caption banner classification (wide + short + low + has a rim).
    caption_min_w_frac: float = 0.50
    caption_max_h_frac: float = 0.14
    caption_min_y_frac: float = 0.45
    caption_min_rim: float = 50.0
    # Cutout aspect-ratio sanity band (w/h).
    aspect_min: float = 0.20
    aspect_max: float = 5.0
    # Overall confidence needed for ``matched``.
    confidence_threshold: float = 0.45
    # Temporal pulse (exec): a frame diff above this delta is "changed".
    pulse_delta: int = 25
    # The changed region must fall within this fraction of the frame to count
    # as a localized pulse (rejects whole-screen transitions / scene loads).
    pulse_max_frac: float = 0.30
    pulse_min_frac: float = 0.0005


_DEFAULT_CONFIG = SpotlightConfig()


@dataclass(frozen=True, slots=True)
class SpotlightHit:
    """Result of :func:`detect_tutorial_spotlight`."""

    matched: bool
    confidence: float
    # Tap target (absolute px) — the compact cutout centroid; (-1,-1) if none.
    cx: int
    cy: int
    cutout_bbox: tuple[int, int, int, int]  # x, y, w, h (0s if no cutout)
    scrim_frac: float
    rim_step: float
    has_caption: bool
    blob_count: int
    reason: str


@dataclass(frozen=True, slots=True)
class _Blob:
    x: int
    y: int
    w: int
    h: int
    area: int
    cx: float
    cy: float
    rim_step: float


def _close_kernel(width: int) -> np.ndarray:
    k = max(11, (width // 48) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def _rim_step(value: np.ndarray, x: int, y: int, w: int, h: int, pad: int) -> float:
    """Inside-bbox mean V minus the mean V of a padded ring just outside it.

    Returns 0.0 when the ring collapses (cutout fills the padded area).
    """
    height, width = value.shape[:2]
    ox1 = max(0, x - pad)
    oy1 = max(0, y - pad)
    ox2 = min(width, x + w + pad)
    oy2 = min(height, y + h + pad)
    outer = value[oy1:oy2, ox1:ox2].astype(np.float32).copy()
    ix1, iy1 = x - ox1, y - oy1
    outer[iy1 : iy1 + h, ix1 : ix1 + w] = np.nan
    ring_vals = outer[~np.isnan(outer)]
    if ring_vals.size == 0:
        return 0.0
    inside = float(value[y : y + h, x : x + w].mean())
    return inside - float(ring_vals.mean())


def detect_tutorial_spotlight(
    image_bgr: np.ndarray,
    config: SpotlightConfig | None = None,
) -> SpotlightHit:
    """Detect the dim-overlay + bright-cutout tutorial pattern on a single frame.

    Returns a :class:`SpotlightHit`; ``matched`` is True only when a dim scrim
    encloses one compact, sharp-rimmed bright cutout. The cutout centroid is the
    tap target.
    """
    cfg = config or _DEFAULT_CONFIG
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return SpotlightHit(False, 0.0, -1, -1, (0, 0, 0, 0), 0.0, 0.0, False, 0, "no_frame")

    height, width = image_bgr.shape[:2]
    frame_area = float(width * height)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    value = hsv[..., 2]

    scrim_frac = float((value <= cfg.v_scrim).mean())

    bright = ((value >= cfg.v_bright).astype(np.uint8)) * 255
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, _close_kernel(width))
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(bright, 8)

    pad = max(1, int(cfg.ring_pad_frac * width))
    cutouts: list[_Blob] = []
    has_caption = False
    blob_count = 0
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w <= 0 or h <= 0 or area <= 0:
            continue
        frac = area / frame_area
        if frac < cfg.min_cutout_frac or frac > cfg.max_cutout_frac:
            continue
        blob_count += 1
        cx, cy = float(centroids[i][0]), float(centroids[i][1])
        step = _rim_step(value, x, y, w, h, pad)
        # Caption banner: a wide, short, low bright band with a rim. It is a
        # signal the tutorial is up, but never the tap target.
        is_caption = (
            w >= cfg.caption_min_w_frac * width
            and h <= cfg.caption_max_h_frac * height
            and cy >= cfg.caption_min_y_frac * height
            and step >= cfg.caption_min_rim
        )
        if is_caption:
            has_caption = True
            continue
        aspect = w / float(h)
        if step < cfg.rim_step_min or aspect < cfg.aspect_min or aspect > cfg.aspect_max:
            continue
        cutouts.append(_Blob(x, y, w, h, area, cx, cy, step))

    # The strongest-rimmed compact cutout is the tap target.
    cutout = max(cutouts, key=lambda b: b.rim_step) if cutouts else None

    scrim_score = float(
        np.clip((scrim_frac - cfg.scrim_lo) / max(1e-6, cfg.scrim_hi - cfg.scrim_lo), 0.0, 1.0)
    )
    cutout_score = (
        float(np.clip(cutout.rim_step / max(1e-6, cfg.rim_full), 0.0, 1.0)) if cutout else 0.0
    )
    caption_score = 1.0 if has_caption else 0.0
    confidence = 0.5 * cutout_score + 0.3 * scrim_score + 0.2 * caption_score

    if blob_count > cfg.max_bright_blobs:
        return SpotlightHit(
            False, confidence, -1, -1, (0, 0, 0, 0), scrim_frac, 0.0,
            has_caption, blob_count, "too_many_bright_blobs",
        )
    if cutout is None:
        return SpotlightHit(
            False, confidence, -1, -1, (0, 0, 0, 0), scrim_frac, 0.0,
            has_caption, blob_count, "no_cutout",
        )
    if scrim_frac < cfg.scrim_floor:
        return SpotlightHit(
            False, confidence, int(round(cutout.cx)), int(round(cutout.cy)),
            (cutout.x, cutout.y, cutout.w, cutout.h), scrim_frac, cutout.rim_step,
            has_caption, blob_count, "no_scrim",
        )
    matched = confidence >= cfg.confidence_threshold
    return SpotlightHit(
        matched,
        confidence,
        int(round(cutout.cx)),
        int(round(cutout.cy)),
        (cutout.x, cutout.y, cutout.w, cutout.h),
        scrim_frac,
        cutout.rim_step,
        has_caption,
        blob_count,
        "spotlight" if matched else "low_confidence",
    )


@dataclass(frozen=True, slots=True)
class PulseTarget:
    """A localized inter-frame animation — the surest tutorial tap target."""

    found: bool
    cx: int
    cy: int
    bbox: tuple[int, int, int, int]
    changed_frac: float


def find_pulse_target(
    prev_bgr: np.ndarray,
    cur_bgr: np.ndarray,
    config: SpotlightConfig | None = None,
) -> PulseTarget:
    """Locate the pulsing highlight by diffing two frames.

    During a tutorial the screen is frozen except the breathing highlight, so
    the changed pixels concentrate on the tap target. Rejects whole-screen
    transitions (changed_frac out of band) and requires the change to land on a
    lit region (the highlight is bright), so a moving NPC behind a thin scrim
    cannot hijack the target.
    """
    cfg = config or _DEFAULT_CONFIG
    if (
        prev_bgr is None
        or cur_bgr is None
        or prev_bgr.shape != cur_bgr.shape
        or prev_bgr.ndim != 3
    ):
        return PulseTarget(False, -1, -1, (0, 0, 0, 0), 0.0)

    height, width = cur_bgr.shape[:2]
    frame_area = float(width * height)
    delta = cv2.absdiff(prev_bgr, cur_bgr).max(axis=2)
    changed = delta > cfg.pulse_delta
    changed_frac = float(changed.mean())
    if changed_frac < cfg.pulse_min_frac or changed_frac > cfg.pulse_max_frac:
        return PulseTarget(False, -1, -1, (0, 0, 0, 0), changed_frac)

    ys, xs = np.where(changed)
    if xs.size == 0:
        return PulseTarget(False, -1, -1, (0, 0, 0, 0), changed_frac)
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    if (bw * bh) / frame_area > cfg.pulse_max_frac:
        # Changed pixels span a large bbox (diffuse) — not a compact pulse.
        return PulseTarget(False, -1, -1, (x1, y1, bw, bh), changed_frac)
    cx, cy = int(round(float(xs.mean()))), int(round(float(ys.mean())))
    # The highlight is bright; reject a pulse centred on a dark area.
    value = cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2HSV)[..., 2]
    if int(value[cy, cx]) < cfg.v_scrim:
        return PulseTarget(False, cx, cy, (x1, y1, bw, bh), changed_frac)
    return PulseTarget(True, cx, cy, (x1, y1, bw, bh), changed_frac)


def debug_overlay(image_bgr: np.ndarray, hit: SpotlightHit) -> np.ndarray:
    """Render the scrim tint + cutout box + tap centroid for inspection.

    Mirrors :meth:`popup.mask.SharpnessMask.debug_overlay`: blue tint = the dim
    scrim, green box = the bright cutout, red dot = the tap target. Used by the
    ``botctl detection`` / labeling preview to confirm a real tutorial frame —
    drawn ON the real screenshot, never a redrawn schematic.
    """
    vis = image_bgr.copy()
    value = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)[..., 2]
    scrim = value <= _DEFAULT_CONFIG.v_scrim
    tint = np.zeros_like(vis)
    tint[scrim] = (255, 0, 0)  # blue in BGR
    vis = cv2.addWeighted(vis, 1.0, tint, 0.25, 0.0)
    x, y, w, h = hit.cutout_bbox
    if w > 0 and h > 0:
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
    if hit.cx >= 0 and hit.cy >= 0:
        cv2.circle(vis, (hit.cx, hit.cy), 10, (0, 0, 255), 2)
        cv2.drawMarker(vis, (hit.cx, hit.cy), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
    label = (
        f"{hit.reason} conf={hit.confidence:.2f} scrim={hit.scrim_frac:.2f} "
        f"rim={hit.rim_step:.0f} cap={int(hit.has_caption)} blobs={hit.blob_count}"
    )
    cv2.putText(vis, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
    cv2.putText(vis, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return vis
