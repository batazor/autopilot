"""ADB serial normalization."""
from __future__ import annotations


def canonical_adb_serial(s: str) -> str:
    """Collapse ``emulator-<N>`` ↔ ``127.0.0.1:<N+1>`` to one form.

    ADB's auto-scan registers an emulator both ways. Callers comparing a
    configured ``bluestacks_window_title`` against live ``adb devices`` should
    canonicalise both sides so the two notations match.
    """
    s = (s or "").strip()
    if s.startswith("emulator-"):
        try:
            n = int(s.split("-", 1)[1])
            return f"127.0.0.1:{n + 1}"
        except (ValueError, IndexError):
            pass
    return s


def is_emulator_adb_serial(serial: str) -> bool:
    """True for BlueStacks / SDK emulators (localhost or ``emulator-*``)."""
    s = (serial or "").strip()
    if s.startswith("emulator-"):
        return True
    return canonical_adb_serial(s).startswith("127.0.0.1:")

