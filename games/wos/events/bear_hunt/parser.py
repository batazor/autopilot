"""Parse the Bear Hunt trap cooldown timer off the event info page.

Each trap tab (Trap 1 / Trap 2) shows a red ``On cooldown: HH:MM:SS`` line under
the description — or ``On cooldown: Nd HH:MM:SS`` once the remaining time is ≥ a
day. The text sits at a fixed band (the info page is a static layout, unlike the
calendar popup which floats), so the reader crops :data:`COOLDOWN_BBOX` and OCRs
it with the ``line`` preprocess, which keeps the ``:`` separators (verified clean
on a live device: ``On cooldown: 19:40:46`` / ``On cooldown: 1d 13:29:45``).

:func:`parse_cooldown` is pure and unit-tested. It returns ``None`` when the line
holds no cooldown (the trap is ready / the active-window timer is showing instead)
— the caller treats that as "ready now".
"""
from __future__ import annotations

import re
from datetime import timedelta

# Fixed on-screen geometry (1280×720 portrait). (x0, y0, x1, y1).
COOLDOWN_BBOX = (220, 1100, 560, 1142)
# "Lv. N" sits above the Trap Enhancement icon on the info page.
LEVEL_BBOX = (592, 1098, 680, 1140)
# Tap centres of the two trap tabs (the active tab is rendered lighter, but we
# tap both explicitly rather than infer which is active).
TRAP_TAPS: dict[str, tuple[int, int]] = {"1": (190, 795), "2": (510, 795)}
# OCR preprocess that preserves the ``:`` separators in the timer.
COOLDOWN_PREPROCESS = "line"
# "Lv. N" reads cleanly with the title preprocess ("L" is often dropped — we only
# need the digit).
LEVEL_PREPROCESS = "title_line"
# Trap Enhancement is capped at level 5 ("Lv. 5 (Maxed)" in-game).
MAX_LEVEL = 5

_LEVEL_RE = re.compile(r"(\d+)")

# ``On cooldown:`` followed by an optional ``Nd`` day count then ``HH:MM:SS``.
# Separators are read as ``:`` by the ``line`` preprocess, but accept ``.``/space
# too in case of OCR jitter. The ``Nd`` group requires the ``d`` to be preceded
# by digits and followed by a space, so the ``d`` inside "cooldown" never matches.
_COOLDOWN_RE = re.compile(
    r"(?:(\d+)\s*d\s+)?(\d{1,2})\s*[:.\s]\s*(\d{2})\s*[:.\s]\s*(\d{2})"
)


def parse_cooldown(text: str) -> timedelta | None:
    """Remaining cooldown as a ``timedelta``, or ``None`` if the trap is ready.

    Requires the line to actually be a cooldown line (contains ``cool``) so the
    active-window countdown — same ``HH:MM:SS`` shape, different meaning — isn't
    misread as a cooldown.
    """
    if not text or "cool" not in text.lower():
        return None
    m = _COOLDOWN_RE.search(text)
    if m is None:
        return None
    days = int(m.group(1)) if m.group(1) else 0
    hours, minutes, seconds = int(m.group(2)), int(m.group(3)), int(m.group(4))
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_level(text: str) -> int | None:
    """Trap Enhancement level from a ``Lv. N`` OCR read, or ``None`` if absent.

    The ``L`` is often dropped by OCR (``"v 5"``), so we just take the digit.
    A trap is maxed when its level reaches :data:`MAX_LEVEL`.
    """
    if not text:
        return None
    m = _LEVEL_RE.search(text)
    return int(m.group(1)) if m else None
