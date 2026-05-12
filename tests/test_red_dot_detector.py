"""Pixel-level checks for ``layout.red_dot_detector``.

Two layers of coverage:

* Synthetic frames lock in the discrimination rules — real circular badge → True,
  stretched red banner / red text / non-red dot → False.
* A captured 720×1280 ``main_city_v2`` frame ensures the detector still fires on
  the real game UI (bottom-bar badges, worker count, mail envelope, Build:Hero
  Hall hammer, …) and stays quiet on bbox'es that hold no notification.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from layout.red_dot_detector import (
    REFERENCE_IMAGE_HEIGHT,
    find_red_dots,
    has_frost_badge,
    has_red_dot_in_bbox_percent,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_CITY_V2_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "main_city_v2_red_dots.png"
FROST_WORKERS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "red_dot_frost_workers.png"
EVENT_1ST_PURCHASE_FALSE_POSITIVE = (
    REPO_ROOT / "tests" / "fixtures" / "event_block_1st_purchase_false_positive.png"
)

REFERENCE_W = 720
REFERENCE_H = REFERENCE_IMAGE_HEIGHT


def _blank_frame(w: int = REFERENCE_W, h: int = REFERENCE_H) -> np.ndarray:
    """Dark teal-blue UI background — emulates the saturated panel a real
    notification badge sits on. The detector's surround-saturation gate
    expects ring S ≥ 45; pure grey (S=0) would trip the gate and turn every
    synthetic dot into a false negative."""
    img = np.full((h, w, 3), (90, 60, 30), dtype=np.uint8)  # BGR → HSV S≈170
    return img


def _draw_red_dot(img: np.ndarray, cx: int, cy: int, radius: int) -> None:
    cv2.circle(img, (cx, cy), radius, (40, 40, 230), thickness=-1)


def _draw_red_dot_with_digit(
    img: np.ndarray, cx: int, cy: int, radius: int, digit: str
) -> None:
    """Notification badge: filled red circle with a white digit on top.

    Reproduces the in-game "unread counter" pattern (e.g. ``mailBox`` and
    ``Build:Hero Hall`` in the captured ``main_city_v2`` frame). The digit
    creates a hole inside the red blob; the detector must still see it as one
    circular contour thanks to ``MORPH_CLOSE`` + ``RETR_EXTERNAL``.
    """
    cv2.circle(img, (cx, cy), radius, (40, 40, 230), thickness=-1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.3, radius / 22.0)
    thickness = max(1, radius // 8)
    (tw, th), _ = cv2.getTextSize(digit, font, font_scale, thickness)
    cv2.putText(
        img,
        digit,
        (cx - tw // 2, cy + th // 2),
        font,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def _bbox_percent(left: int, top: int, w: int, h: int, *, frame_w: int, frame_h: int) -> dict[str, float]:
    return {
        "x": 100.0 * left / frame_w,
        "y": 100.0 * top / frame_h,
        "width": 100.0 * w / frame_w,
        "height": 100.0 * h / frame_h,
    }


def test_find_red_dots_detects_single_circular_badge() -> None:
    img = _blank_frame()
    _draw_red_dot(img, cx=400, cy=300, radius=10)

    dots = find_red_dots(img, image_h_for_norm=img.shape[0])

    assert len(dots) == 1, f"expected 1 dot, got {dots}"
    d = dots[0]
    assert abs(d.cx - 400) <= 2
    assert abs(d.cy - 300) <= 2
    assert 8.0 <= d.radius <= 12.0


def test_find_red_dots_detects_badge_with_counter_digit() -> None:
    """Counter badges (red circle + white digit, like ``mailBox`` "1") still
    register as a single circular detection — ``MORPH_CLOSE`` re-bridges the
    digit hole and ``RETR_EXTERNAL`` reports one outer contour for the ring."""
    img = _blank_frame()
    _draw_red_dot_with_digit(img, cx=400, cy=300, radius=12, digit="1")

    dots = find_red_dots(img, image_h_for_norm=img.shape[0])

    assert len(dots) == 1, f"expected 1 dot, got {dots}"
    d = dots[0]
    assert abs(d.cx - 400) <= 3
    assert abs(d.cy - 300) <= 3


def test_find_red_dots_detects_badge_with_two_digit_counter() -> None:
    """Two-digit counters ("12", "99") also stay one circular detection."""
    img = _blank_frame()
    _draw_red_dot_with_digit(img, cx=400, cy=300, radius=14, digit="12")

    dots = find_red_dots(img, image_h_for_norm=img.shape[0])

    assert len(dots) == 1, f"expected 1 dot for two-digit counter, got {dots}"


def test_find_red_dots_ignores_non_red_circle() -> None:
    img = _blank_frame()
    cv2.circle(img, (400, 300), 10, (230, 40, 40), thickness=-1)

    assert find_red_dots(img, image_h_for_norm=img.shape[0]) == []


def test_find_red_dots_ignores_red_banner() -> None:
    img = _blank_frame()
    cv2.rectangle(img, (200, 600), (520, 615), (40, 40, 230), thickness=-1)

    assert find_red_dots(img, image_h_for_norm=img.shape[0]) == []


def test_find_red_dots_ignores_too_large_red_blob() -> None:
    img = _blank_frame()
    cv2.circle(img, (400, 300), 80, (40, 40, 230), thickness=-1)

    assert find_red_dots(img, image_h_for_norm=img.shape[0]) == []


def test_has_red_dot_in_bbox_percent_true_when_dot_inside() -> None:
    img = _blank_frame()
    _draw_red_dot(img, cx=400, cy=300, radius=10)

    bbox = _bbox_percent(360, 260, 80, 80, frame_w=REFERENCE_W, frame_h=REFERENCE_H)
    assert has_red_dot_in_bbox_percent(img, bbox) is True


def test_has_red_dot_in_bbox_percent_false_when_dot_outside_bbox() -> None:
    img = _blank_frame()
    _draw_red_dot(img, cx=400, cy=300, radius=10)

    bbox = _bbox_percent(10, 10, 80, 80, frame_w=REFERENCE_W, frame_h=REFERENCE_H)
    assert has_red_dot_in_bbox_percent(img, bbox) is False


def test_has_red_dot_in_bbox_percent_false_for_blank_screen() -> None:
    img = _blank_frame()
    bbox = _bbox_percent(360, 260, 80, 80, frame_w=REFERENCE_W, frame_h=REFERENCE_H)
    assert has_red_dot_in_bbox_percent(img, bbox) is False


def test_has_red_dot_handles_invalid_inputs() -> None:
    bbox = _bbox_percent(0, 0, 50, 50, frame_w=REFERENCE_W, frame_h=REFERENCE_H)
    assert has_red_dot_in_bbox_percent(np.zeros((0, 0, 3), dtype=np.uint8), bbox) is False
    assert has_red_dot_in_bbox_percent(_blank_frame(), {}) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Real-frame regression: ``tests/fixtures/main_city_v2_red_dots.png``
# ---------------------------------------------------------------------------
#
# Reference badges in the captured 720×1280 frame (visually verified).
# The two badges marked (counter) carry a white digit on top of the red disc;
# they cover the "unread mail" and "build queue full" UI states. The detector
# treats them like plain dots — ``MORPH_CLOSE`` heals the digit hole.
#
#   * avatar profile circle                        ≈ (87,    10)
#   * workers count "6/8"                          ≈ (373,   12)
#   * Build: Hero Hall hammer        (counter "1") ≈ ( 67, 1029)
#   * mail envelope (bottom-right)   (counter "1") ≈ (692, 1018)
#   * Exploration tab icon                         ≈ ( 99, 1189)
#   * Heroes tab icon                              ≈ (217, 1189)
#
# False-positive guards:
# * Cooked-meat icon at ≈(590, 279) is pinkish-orange (HSV S≈170, V≈204) —
#   the median-S/V floor on matched pixels keeps it out.
# * A bright-red dot at ≈(693, 218) sits on the Davy hero avatar, which is
#   rendered against open sky (surround S≈30). Pixel-perfect notification
#   colour (H=0, S=225, V=255) but the surround-saturation gate rejects it
#   because no real button/icon backs it.

ALL_KNOWN_BADGES_PX: list[tuple[str, int, int]] = [
    ("avatar",         87,   10),
    ("workers",       373,   12),
    ("build_hammer",   67, 1029),
    ("mail_envelope", 692, 1018),
    ("tab_explore",    99, 1189),
    ("tab_heroes",    217, 1189),
]


def _load_main_city_v2() -> np.ndarray:
    img = cv2.imread(str(MAIN_CITY_V2_FIXTURE))
    assert img is not None, f"failed to load fixture: {MAIN_CITY_V2_FIXTURE}"
    return img


def test_real_main_city_v2_detects_exactly_six_badges() -> None:
    """Hard count: the captured frame holds exactly 6 red-dot indicators.
    A drift here means either we lost a true badge or a false-positive snuck in.
    """
    img = _load_main_city_v2()
    dots = find_red_dots(img, image_h_for_norm=img.shape[0])

    assert len(dots) == len(ALL_KNOWN_BADGES_PX), (
        f"expected {len(ALL_KNOWN_BADGES_PX)} red-dot detections, got {len(dots)}: "
        f"{[(round(d.cx, 1), round(d.cy, 1), round(d.score, 3)) for d in dots]}"
    )

    tolerance_px = 14
    for name, ex, ey in ALL_KNOWN_BADGES_PX:
        hit = any(
            abs(d.cx - ex) <= tolerance_px and abs(d.cy - ey) <= tolerance_px
            for d in dots
        )
        assert hit, (
            f"no red-dot detection within ±{tolerance_px}px of `{name}` ({ex},{ey}); "
            f"got {[(round(d.cx, 1), round(d.cy, 1)) for d in dots]}"
        )


def test_real_main_city_v2_has_red_dot_in_known_bboxes() -> None:
    """The 4 most prominent badges, framed as ``area.json``-style percent bboxes."""
    img = _load_main_city_v2()
    h, w = img.shape[:2]

    cases: list[tuple[str, dict[str, float]]] = [
        ("mailBox",       _bbox_percent(610, 980, 90, 100, frame_w=w, frame_h=h)),
        ("buildHeroHall", _bbox_percent(10,  980, 90, 100, frame_w=w, frame_h=h)),
        ("avatarBadge",   _bbox_percent(36,    0, 110, 50, frame_w=w, frame_h=h)),
        ("workersBadge",  _bbox_percent(324,   0, 110, 50, frame_w=w, frame_h=h)),
    ]
    for name, bbox in cases:
        assert has_red_dot_in_bbox_percent(img, bbox) is True, (
            f"expected red dot inside `{name}` bbox {bbox} on main_city_v2"
        )


def test_real_main_city_v2_has_no_red_dot_in_empty_central_area() -> None:
    """Stone tower / open ground in the middle of the frame holds no badge."""
    img = _load_main_city_v2()
    h, w = img.shape[:2]
    bbox = _bbox_percent(252, 380, 216, 320, frame_w=w, frame_h=h)
    assert has_red_dot_in_bbox_percent(img, bbox) is False


def test_real_main_city_v2_rejects_davy_avatar_red_dot() -> None:
    """A pixel-perfect red dot at ≈(693, 218) sits on the Davy hero avatar
    rendered against open sky (surround S≈30). Hue/sat/val are identical to
    real notification badges (H=0, S=225, V=255) — only the unsaturated
    surround tells them apart. The surround-saturation gate must keep it out
    of the ``main_city.event.block.2`` bbox."""
    img = _load_main_city_v2()
    h, w = img.shape[:2]
    bbox = _bbox_percent(629, 201, 83, 91, frame_w=w, frame_h=h)
    assert has_red_dot_in_bbox_percent(img, bbox, accept_frost=False) is False


def test_real_main_city_v2_rejects_pinkish_meat_icon() -> None:
    """Cooked-meat icon at ≈(590, 279) is roughly red-shaped and round, but
    sits in the pink/salmon HSV band (S≈170, V≈204). The median-S/V floor on
    matched pixels must keep it out — it is *not* a notification badge."""
    img = _load_main_city_v2()
    h, w = img.shape[:2]
    bbox = _bbox_percent(560, 250, 60, 60, frame_w=w, frame_h=h)
    assert has_red_dot_in_bbox_percent(img, bbox) is False


# ---------------------------------------------------------------------------
# Winter-event "frost badge" variant
# ---------------------------------------------------------------------------
#
# ``red_dot_frost_workers.png`` is the labeled ``isWorkers`` crop captured
# during a frost event: the normally-red "6/8" counter is rendered as an icy
# cyan capsule with magenta sparkle particles. ``find_red_dots`` cannot find
# it (the badge isn't red anymore) but ``has_frost_badge`` recognises the
# cyan + pink-sparkle conjunction. This must NOT trigger on plain non-event
# screens that have lots of cyan but no pink sparkles.


def _load_frost_workers() -> np.ndarray:
    img = cv2.imread(str(FROST_WORKERS_FIXTURE))
    assert img is not None, f"failed to load fixture: {FROST_WORKERS_FIXTURE}"
    return img


def test_has_frost_badge_true_on_frost_workers_crop() -> None:
    img = _load_frost_workers()
    assert has_frost_badge(img) is True


def test_find_red_dots_does_not_register_frost_workers_as_red() -> None:
    """Frost variant has only ~10 red-mask pixels: the existing red detector
    must stay quiet on it (regression guard if frost handling is removed)."""
    img = _load_frost_workers()
    assert find_red_dots(img, image_h_for_norm=REFERENCE_IMAGE_HEIGHT) == []


def test_has_red_dot_in_bbox_percent_picks_up_frost_workers() -> None:
    """End-to-end: when the labeled bbox itself is the frost crop (i.e. the
    full-frame call would extract this exact patch), ``has_red_dot_in_bbox_percent``
    treats the icy variant as a positive — the unified API is what UI/DSL use."""
    img = _load_frost_workers()
    h, w = img.shape[:2]
    bbox = _bbox_percent(0, 0, w, h, frame_w=w, frame_h=h)
    assert has_red_dot_in_bbox_percent(img, bbox) is True


def test_has_red_dot_in_bbox_percent_accept_frost_off_skips_frost_variant() -> None:
    """Strict red-only callers can opt out of the frost backend with
    ``accept_frost=False`` — the frost crop then reads as no-indicator."""
    img = _load_frost_workers()
    h, w = img.shape[:2]
    bbox = _bbox_percent(0, 0, w, h, frame_w=w, frame_h=h)
    assert has_red_dot_in_bbox_percent(img, bbox, accept_frost=False) is False


def test_has_frost_badge_false_on_plain_main_city_v2_bboxes() -> None:
    """Regression: every notification bbox tested elsewhere has 0 magenta
    sparkles, so the conjunction gate must keep frost detection quiet on
    ordinary play screens — even when the patch happens to be cyan-rich
    (e.g. ``buildHeroHall`` ≈ 27 % cyan)."""
    img = _load_main_city_v2()
    h, w = img.shape[:2]
    cases = [
        ("mailBox",       (610, 980, 90, 100)),
        ("buildHeroHall", (10,  980, 90, 100)),
        ("avatarBadge",   (36,    0, 110, 50)),
        ("workersBadge",  (324,   0, 110, 50)),
        ("empty_center",  (252, 380, 216, 320)),
        ("meat_icon",     (560, 250, 60, 60)),
    ]
    for name, (L, T, W, Hb) in cases:
        patch = img[T:T + Hb, L:L + W]
        assert has_frost_badge(patch) is False, (
            f"main_city_v2 bbox `{name}` must not register as frost badge"
        )


# ---------------------------------------------------------------------------
# Regression: "1st Purchase" event icon false positive
# ---------------------------------------------------------------------------
#
# The 1st Purchase event icon at ``main_city.event.block.2`` is a character
# portrait pasted over the snowy city background. Naively, ``has_frost_badge``
# saw lots of cyan (snow/sky behind the icon) AND a sprinkle of pink pixels
# (character hair / dress edges) and fired — even though the pink wasn't
# anywhere near the cyan. The captured patch is preserved as a fixture so
# future tweaks to the frost detector keep this case quiet.


def test_has_frost_badge_false_on_1st_purchase_event_icon() -> None:
    """Cyan background + pink character detail away from cyan must NOT fire."""
    patch = cv2.imread(str(EVENT_1ST_PURCHASE_FALSE_POSITIVE))
    assert patch is not None, f"failed to load fixture: {EVENT_1ST_PURCHASE_FALSE_POSITIVE}"
    assert has_frost_badge(patch) is False, (
        "1st Purchase event icon (cyan background + far-away pink) "
        "must not register as a frost badge"
    )
