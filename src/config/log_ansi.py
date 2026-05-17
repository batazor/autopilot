"""ANSI fragments inside log messages (e.g. DSL scenario names).

Respects ``NO_COLOR`` and TTY detection via :func:`config.logging_stdout.stdout_supports_ansi_color`.
"""
from __future__ import annotations

from config.logging_stdout import stdout_supports_ansi_color

_RESET = "\x1b[0m"
_SCENARIO_FG = "\x1b[36m"  # cyan


def scenario_log_label(name: str) -> str:
    """Highlight a scenario stem for terminal logs; plain text when coloring is off."""
    n = str(name or "").strip()
    if not n:
        return ""
    if not stdout_supports_ansi_color():
        return n
    return f"{_SCENARIO_FG}{n}{_RESET}"
