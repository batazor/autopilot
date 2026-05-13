"""Visual sanity test for ``navigation.hero_grid_search``.

Runs :func:`scan_grid_frame` against ``references/page.heroes.png`` and
renders a debug overlay showing every per-cell detection: template box,
``"Lv. X"`` / shard-counter regions, red-dot patch, upgrade-arrow patch,
plus the boolean flags written to ``heroes.entries.<id>`` by the
``scan_heroes_grid`` DSL exec.

Output: a single PNG at ``/tmp/heroes_grid_debug.png`` plus a per-cell
table printed to stdout. Use it to validate offsets after the wiki icons
or in-game layout change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from navigation.hero_grid_search import HeroMatch, scan_grid_frame

_REF = "references/page.heroes.png"
_OUT = "/tmp/heroes_grid_debug.png"

# BGR colors
_C_TEMPLATE_OK = (0, 200, 0)       # green: hero matched (unlocked)
_C_TEMPLATE_LOCKED = (180, 130, 0) # teal: hero matched (locked)
_C_LEVEL = (0, 200, 200)           # yellow: Lv badge OCR region
_C_BADGE = (200, 200, 0)           # cyan: shard counter OCR region
_C_RED_DOT_ON = (0, 0, 255)        # red filled: notification present
_C_RED_DOT_OFF = (90, 90, 90)      # gray: notification slot, none
_C_UPGRADE_ON = (60, 220, 0)       # bright green filled: upgrade ready
_C_UPGRADE_OFF = (90, 90, 90)      # gray: upgrade slot, none


def _draw_rect(
    img: np.ndarray,
    bbox: tuple[int, int, int, int],
    color: tuple[int, int, int],
    thickness: int = 1,
) -> None:
    x, y, w, h = bbox
    cv2.rectangle(img, (x, y), (x + w, y + h), color, thickness)


def _draw_filled_tint(
    img: np.ndarray,
    bbox: tuple[int, int, int, int],
    color: tuple[int, int, int],
    alpha: float = 0.35,
) -> None:
    x, y, w, h = bbox
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(img.shape[1], x + w), min(img.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    overlay = img[y0:y1, x0:x1].copy()
    overlay[:] = color
    img[y0:y1, x0:x1] = cv2.addWeighted(
        overlay, alpha, img[y0:y1, x0:x1], 1.0 - alpha, 0.0
    )


def _label(
    img: np.ndarray,
    text: str,
    org: tuple[int, int],
    color: tuple[int, int, int],
    scale: float = 0.4,
) -> None:
    # Draw a thin black outline first so the label survives any background.
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def _render(frame: np.ndarray, hits: dict[str, HeroMatch]) -> np.ndarray:
    out = frame.copy()
    for hid, m in hits.items():
        cx, cy = m.xy
        tpl_box = (cx - 75, cy - 75, 150, 150)
        tpl_color = _C_TEMPLATE_OK if m.available else _C_TEMPLATE_LOCKED
        _draw_rect(out, tpl_box, tpl_color, thickness=2)

        if m.available:
            _draw_rect(out, m.level_bbox, _C_LEVEL, thickness=1)
        else:
            _draw_rect(out, m.badge_bbox, _C_BADGE, thickness=1)

        if m.has_red_dot:
            _draw_filled_tint(out, m.red_dot_bbox, _C_RED_DOT_ON, alpha=0.45)
            _draw_rect(out, m.red_dot_bbox, _C_RED_DOT_ON, thickness=2)
        else:
            _draw_rect(out, m.red_dot_bbox, _C_RED_DOT_OFF, thickness=1)

        if m.upgrade_available:
            _draw_filled_tint(out, m.upgrade_bbox, _C_UPGRADE_ON, alpha=0.55)
            _draw_rect(out, m.upgrade_bbox, _C_UPGRADE_ON, thickness=2)
        else:
            _draw_rect(out, m.upgrade_bbox, _C_UPGRADE_OFF, thickness=1)

        ri, ci = m.cell
        head = f"r{ri}c{ci} {hid} {m.score:.2f}"
        _label(out, head, (cx - 70, cy - 78), tpl_color, scale=0.40)

        flags = []
        if m.available:
            flags.append("OPEN")
        else:
            flags.append("LOCK")
        if m.has_red_dot:
            flags.append("DOT")
        if m.upgrade_available:
            flags.append("UP")
        _label(out, " ".join(flags), (cx - 70, cy + 70), tpl_color, scale=0.38)
    return out


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    ref_path = repo / _REF
    frame = cv2.imread(str(ref_path))
    if frame is None:
        print(f"cannot read {ref_path}", file=sys.stderr)
        return 2

    hits = scan_grid_frame(frame, threshold=0.7)

    print(f"reference: {ref_path.name}  detected: {len(hits)}")
    print(
        f'{"hero":14s} {"ncc":>5s} {"avail":>5s} {"dot":>4s} {"upg":>4s} '
        f'  xy            level_bbox             upgrade_bbox          red_dot_bbox'
    )
    for hid in sorted(hits):
        m = hits[hid]
        print(
            f"{hid:14s} {m.score:5.3f} {str(m.available)[:1]:>5s} "
            f"{str(m.has_red_dot)[:1]:>4s} {str(m.upgrade_available)[:1]:>4s}  "
            f"{m.xy}  {m.level_bbox}  {m.upgrade_bbox}  {m.red_dot_bbox}"
        )
    print(
        "\nlegend: green box = unlocked match · teal box = locked match · "
        "yellow = Lv OCR · cyan = X/Y OCR · red fill = notification · "
        "green fill = upgrade arrow"
    )

    out = _render(frame, hits)
    out_path = Path(_OUT)
    cv2.imwrite(str(out_path), out)
    print(f"overlay saved to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
