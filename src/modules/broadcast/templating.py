"""Pure text helpers for broadcast messages: slug + safe `{placeholder}` render.

No IO, no game imports. The runner builds the substitution context (event name,
hours-to-start, alliance, state) from the calendar cache + player state and calls
:func:`render` right before typing the message.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# A single ``{name}`` placeholder (letters/digits/underscore). Doubled braces are
# left untouched so literal ``{{`` / ``}}`` survive.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def slug(name: str) -> str:
    """Stable identifier from an event name: ``"Foundry Battle" -> "foundry_battle"``.

    Matches ``games.wos.core.calendar.schedule.slug`` semantics, re-implemented
    here so the game-agnostic core doesn't import a per-game module.
    """
    return _SLUG_RE.sub("_", str(name or "").lower()).strip("_")


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        # Trim a trailing ``.0`` so "{in_hours}" reads "2" not "2.0".
        return str(int(value)) if value.is_integer() else f"{value:.1f}"
    return "" if value is None else str(value)


def render(text: str, context: Mapping[str, Any]) -> str:
    """Substitute ``{key}`` placeholders from ``context``.

    Unknown placeholders are left verbatim (so a typo shows up instead of
    silently vanishing). Values are stringified; floats drop a trailing ``.0``.
    """
    if not text:
        return text

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in context:
            return _format_value(context[key])
        return m.group(0)  # leave unknown placeholders as-is

    return _PLACEHOLDER_RE.sub(repl, text)
